terraform {
  required_version = ">= 1.5"

  # Configuración parcial: los valores están en backend.hcl (no se sube a Git)
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "=5.10.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

# Sufijo aleatorio para garantizar un nombre único globalmente
resource "random_string" "suffix" {
  length  = 8
  upper   = false
  special = false
}

# Recurso de Microsoft Foundry (Cognitive Services kind AIServices)
resource "azurerm_cognitive_account" "foundry" {
  name                = "${var.foundry_name}-${random_string.suffix.result}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  kind                = "AIServices"
  sku_name            = "S0"

  # Necesario para poder crear proyectos de Foundry
  project_management_enabled = true
  custom_subdomain_name      = "${var.foundry_name}-${random_string.suffix.result}"

  identity {
    type = "SystemAssigned"
  }
}

# Proyecto de Foundry dentro del recurso
resource "azurerm_cognitive_account_project" "this" {
  name                 = var.project_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_resource_group.this.location

  identity {
    type = "SystemAssigned"
  }
}



# Rerank: priorizar la cantidad de documentos obtenidos del RAG
# Solo se ofrece como GlobalStandard (no existe DataZoneStandard)
resource "azurerm_cognitive_deployment" "cohere_rerank_v4_fast" {
  name                 = "Cohere-rerank-v4.0-fast"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "Cohere"
    name    = "Cohere-rerank-v4.0-fast"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 1
  }
}

# Despliegue del modelo gpt-4.1-mini
# Sin cuota en DataZoneStandard (0); se usa Standard (procesa en la región del recurso)
resource "azurerm_cognitive_deployment" "gpt_4_1_mini" {
  name                 = "gpt-4.1-mini"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "Standard"
    capacity = 1
  }
}

# Despliegue del modelo text-embedding-3-small
resource "azurerm_cognitive_deployment" "text_embedding_3_small" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "DataZoneStandard"
    capacity = 1
  }
}

# Endpoint base para el cliente OpenAI/Azure usado por main.py
locals {
  foundry_openai_endpoint = "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com"
}

# Guarda AZURE_OPENAI_ENDPOINT en .env tras cada apply en que cambie el endpoint.
# Solo reemplaza esa variable: el resto del archivo se conserva.
resource "terraform_data" "env_file" {
  triggers_replace = local.foundry_openai_endpoint

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      ENV_FILE = "${path.module}/.env"
      ENDPOINT = local.foundry_openai_endpoint
    }
    command = <<-EOT
      touch "$ENV_FILE"
      { grep -v '^AZURE_OPENAI_ENDPOINT=' "$ENV_FILE" || true; echo "AZURE_OPENAI_ENDPOINT=$ENDPOINT"; } > "$ENV_FILE.tmp"
      mv "$ENV_FILE.tmp" "$ENV_FILE"
    EOT
  }
}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "foundry_endpoint" {
  value = azurerm_cognitive_account.foundry.endpoint
}

output "foundry_project_id" {
  value = azurerm_cognitive_account_project.this.id
}

output "foundry_openai_endpoint" {
  value = local.foundry_openai_endpoint
}

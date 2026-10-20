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
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
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
    capacity = 150
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
    capacity = 50
  }
}

# Despliegue del modelo de voz para la llamada desde el navegador (/voice)
# Único modelo realtime con cuota en la región: gpt-realtime-mini (GlobalStandard, 40)
resource "azurerm_cognitive_deployment" "gpt_realtime_mini" {
  name                 = "gpt-realtime-mini"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = "gpt-realtime-mini"
    version = "2025-12-15"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

# Azure AI Document Intelligence: extracción de texto de PDFs (prebuilt-layout)
resource "azurerm_cognitive_account" "docintel" {
  name                  = "docintel-${var.foundry_name}-${random_string.suffix.result}"
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "FormRecognizer"
  sku_name              = "S0"
  custom_subdomain_name = "docintel-${var.foundry_name}-${random_string.suffix.result}"
}

# Acceso con Entra ID (az login) para el usuario que ejecuta Terraform
data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "me_docintel_user" {
  scope                = azurerm_cognitive_account.docintel.id
  role_definition_name = "Cognitive Services User"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Cuenta de almacenamiento existente (no la crea Terraform) donde main.py guarda el Markdown
data "azurerm_storage_account" "docs" {
  name                = var.storage_account_name
  resource_group_name = coalesce(var.storage_resource_group_name, var.resource_group_name)
}

# Rol de datos necesario para escribir blobs con Entra ID (Owner/Contributor no bastan)
resource "azurerm_role_assignment" "me_storage_blob" {
  scope                = data.azurerm_storage_account.docs.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Azure AI Search: índice vectorial + BM25 de los fragmentos de las guías
resource "azurerm_search_service" "this" {
  name                         = "search-${var.foundry_name}-${random_string.suffix.result}"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = var.search_location # región propia: ver variables.tf
  sku                          = "basic"
  local_authentication_enabled = false  # solo Entra ID, sin API keys
  semantic_search_sku          = "free" # semantic ranker: 1000 consultas/mes gratis
}

# Crear índices (plano de datos de gestión del servicio)
resource "azurerm_role_assignment" "me_search_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Leer y escribir documentos en los índices
resource "azurerm_role_assignment" "me_search_data" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Endpoint base para el cliente OpenAI/Azure usado por main.py
locals {
  foundry_openai_endpoint = "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com"
  docintel_endpoint       = azurerm_cognitive_account.docintel.endpoint
  search_endpoint         = "https://${azurerm_search_service.this.name}.search.windows.net"
}

# Guarda los endpoints en .env tras cada apply en que cambien.
# Solo reemplaza esas variables: el resto del archivo se conserva.
resource "terraform_data" "env_file" {
  triggers_replace = [local.foundry_openai_endpoint, local.docintel_endpoint, local.search_endpoint]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      ENV_FILE                       = "${path.module}/.env"
      AZURE_OPENAI_ENDPOINT          = local.foundry_openai_endpoint
      DOCUMENT_INTELLIGENCE_ENDPOINT = local.docintel_endpoint
      SEARCH_ENDPOINT                = local.search_endpoint
    }
    command = <<-EOT
      touch "$ENV_FILE"
      for var in AZURE_OPENAI_ENDPOINT DOCUMENT_INTELLIGENCE_ENDPOINT SEARCH_ENDPOINT; do
        { grep -v "^$var=" "$ENV_FILE" || true; echo "$var=$${!var}"; } > "$ENV_FILE.tmp"
        mv "$ENV_FILE.tmp" "$ENV_FILE"
      done
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

output "document_intelligence_endpoint" {
  value = local.docintel_endpoint
}

output "search_endpoint" {
  value = local.search_endpoint
}

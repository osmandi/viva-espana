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

## Despliegue del modelo text-embedding-3-small
#resource "azurerm_cognitive_deployment" "text_embedding_3_small" {
#  name                 = "text-embedding-3-small"
#  cognitive_account_id = azurerm_cognitive_account.foundry.id
#
#  model {
#    format  = "OpenAI"
#    name    = "text-embedding-3-small"
#    version = "1"
#  }
#
#  sku {
#    name     = "DataZoneStandard"
#    capacity = 1
#  }
#}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "foundry_endpoint" {
  value = azurerm_cognitive_account.foundry.endpoint
}

output "foundry_project_id" {
  value = azurerm_cognitive_account_project.this.id
}

# ---------------------------------------------------------------------------
# Despliegue de la API (FastAPI) como contenedor en Azure Container Apps
#
# Flujo: terraform apply -> `az acr build` construye la imagen en el propio ACR (no hace
# falta Docker en local) -> el Container App la descarga con una identidad administrada
# y accede a Foundry, Search, Document Intelligence y Blob Storage con Entra ID, sin claves.
# ---------------------------------------------------------------------------

# Registro de contenedores privado (sin usuario administrador: solo identidades con AcrPull)
resource "azurerm_container_registry" "this" {
  name                = "acr${replace(var.app_name, "-", "")}${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
}

# Identidad con la que se ejecuta la aplicación. Es de usuario (no del sistema) para poder
# darle permisos ANTES de crear el Container App y que pueda descargar la imagen al arrancar.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${var.app_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Chat, embeddings y modelo realtime de voz (Foundry)
resource "azurerm_role_assignment" "app_openai_user" {
  scope                = azurerm_cognitive_account.foundry.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# POST /pdf/extract (Document Intelligence)
resource "azurerm_role_assignment" "app_docintel_user" {
  scope                = azurerm_cognitive_account.docintel.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# POST /pdf/extract escribe el Markdown en Blob Storage
resource "azurerm_role_assignment" "app_storage_blob" {
  scope                = data.azurerm_storage_account.docs.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# La API solo consulta el índice (la ingesta la hace ingest.py con el usuario)
resource "azurerm_role_assignment" "app_search_reader" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Los roles tardan en propagarse; sin esta espera el primer arranque puede fallar al
# descargar la imagen (AcrPull) o al llamar a Azure.
resource "time_sleep" "roles" {
  create_duration = "60s"

  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_openai_user,
    azurerm_role_assignment.app_docintel_user,
    azurerm_role_assignment.app_storage_blob,
    azurerm_role_assignment.app_search_reader,
  ]
}

# La etiqueta de la imagen es un hash del código: cambia solo cuando cambia algo que entra
# en la imagen, y eso provoca una imagen nueva y una revisión nueva del Container App.
locals {
  app_image_tag = substr(sha256(join("", [
    filesha256("${path.module}/Dockerfile"),
    filesha256("${path.module}/pyproject.toml"),
    filesha256("${path.module}/uv.lock"),
    filesha256("${path.module}/main.py"),
    filesha256("${path.module}/tools.py"),
    filesha256("${path.module}/static/index.html"),
  ])), 0, 12)
  app_image = "${azurerm_container_registry.this.login_server}/${var.app_name}:${local.app_image_tag}"
}

# Construye la imagen dentro del ACR (ACR Tasks). Requiere `az login`, igual que Terraform.
resource "terraform_data" "app_image" {
  triggers_replace = [local.app_image_tag, azurerm_container_registry.this.id]

  provisioner "local-exec" {
    working_dir = path.module
    command     = "az acr build --registry ${azurerm_container_registry.this.name} --image ${var.app_name}:${local.app_image_tag} --file Dockerfile ."
  }
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${var.app_name}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${var.app_name}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
}

resource "azurerm_container_app" "this" {
  name                         = var.app_name
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  # HTTPS público: el navegador solo da acceso al micrófono en páginas HTTPS.
  # El WebSocket /voice funciona por este mismo ingress.
  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.app_min_replicas
    max_replicas = var.app_max_replicas

    container {
      name   = var.app_name
      image  = local.app_image
      cpu    = 0.5
      memory = "1Gi"

      # Misma configuración que el .env local (ver main.py)
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = local.foundry_openai_endpoint
      }
      env {
        name  = "DOCUMENT_INTELLIGENCE_ENDPOINT"
        value = local.docintel_endpoint
      }
      env {
        name  = "SEARCH_ENDPOINT"
        value = local.search_endpoint
      }
      env {
        name  = "STORAGE_ACCOUNT_NAME"
        value = var.storage_account_name
      }
      # Indica a DefaultAzureCredential qué identidad administrada usar
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }
    }
  }

  depends_on = [
    terraform_data.app_image,
    time_sleep.roles,
  ]
}

output "app_url" {
  description = "URL pública de la aplicación (chat y llamada de voz)."
  value       = "https://${azurerm_container_app.this.ingress[0].fqdn}"
}

output "container_registry" {
  description = "Servidor del registro de contenedores."
  value       = azurerm_container_registry.this.login_server
}

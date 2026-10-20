variable "location" {
  type        = string
  description = "Región de Azure donde se desplegarán los recursos."
  default     = "eastus2"
}

variable "search_location" {
  type        = string
  description = "Región de AI Search. Puede diferir de location: eastus2 no tenía capacidad para servicios de Search nuevos."
  default     = "eastus"
}

variable "resource_group_name" {
  type    = string
  default = "IA"
}

variable "foundry_name" {
  type        = string
  description = "Prefijo del nombre del recurso Foundry (se le añade un sufijo aleatorio; también es el subdominio personalizado)."
  default     = "foundry-viva-espana"
}

variable "project_name" {
  type    = string
  default = "proj-viva-espana"
}

variable "storage_account_name" {
  type        = string
  description = "Nombre de la cuenta de almacenamiento existente donde se guarda el texto extraído de los PDFs."
}

variable "storage_resource_group_name" {
  type        = string
  description = "Resource group de la cuenta de almacenamiento. Si es null, se usa resource_group_name."
  default     = null
}

variable "app_name" {
  type        = string
  description = "Nombre del Container App (y de la imagen) de la API de FastAPI."
  default     = "viva-espana"
}

variable "app_min_replicas" {
  type        = number
  description = "Réplicas mínimas del Container App. Con 0 se apaga sin tráfico, pero la primera llamada de voz espera al arranque."
  default     = 1
}

variable "app_max_replicas" {
  type        = number
  description = "Réplicas máximas del Container App."
  default     = 2
}

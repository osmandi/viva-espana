variable "location" {
  type        = string
  description = "Región de Azure donde se desplegarán los recursos."
  default     = "eastus2"
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

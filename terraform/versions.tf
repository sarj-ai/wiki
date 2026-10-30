terraform {
  required_version = ">= 1.17.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.8.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9.1"
    }
  }

  backend "gcs" {
    bucket = "sarj-docs-terraform-state-dammam"
    prefix = "terraform/state"
  }
}

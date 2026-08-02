terraform {
  required_version = ">= 1.15.8"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.42"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  backend "gcs" {
    bucket = "sarj-docs-terraform-state"
    prefix = "terraform/state"
  }
}

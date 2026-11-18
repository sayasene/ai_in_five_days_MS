terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0.0"
    }
  }

  # Uncomment for shared team remote state (configured via `agents-cli infra cicd`):
  # backend "gcs" {
  #   bucket = "<your-gcp-project-id>-terraform-state"
  #   prefix = "pursuit-auditor-agent/prod"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

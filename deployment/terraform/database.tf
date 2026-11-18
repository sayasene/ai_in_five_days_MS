# -----------------------------------------------------------------------------
# Persistent ADK Session State & pgvector Precedent Store (Cloud SQL PostgreSQL)
# -----------------------------------------------------------------------------

resource "google_sql_database_instance" "sessions_db" {
  name                = "${var.service_name}-${var.environment}-pg"
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.environment == "prod"

  settings {
    tier = var.cloud_sql_tier

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = var.environment == "prod"
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_sql_database" "pursuit_auditor" {
  name     = "pursuit_auditor"
  project  = var.project_id
  instance = google_sql_database_instance.sessions_db.name
}

resource "google_sql_user" "app_iam_user" {
  name     = trimsuffix(google_service_account.app_sa.email, ".gserviceaccount.com")
  project  = var.project_id
  instance = google_sql_database_instance.sessions_db.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

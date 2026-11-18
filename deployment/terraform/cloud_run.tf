# -----------------------------------------------------------------------------
# Artifact Registry & Cloud Run v2 Service with Secret Manager + Cloud SQL + OTel
# -----------------------------------------------------------------------------

resource "google_artifact_registry_repository" "agent_images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.service_name
  description   = "Container images for the Pursuit Deliverable Auditor ADK service."
  format        = "DOCKER"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_cloud_run_v2_service" "pursuit_auditor" {
  name     = "${var.service_name}-${var.environment}"
  project  = var.project_id
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.app_sa.email

    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = var.environment == "prod" ? 10 : 3
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.sessions_db.connection_name]
      }
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "PURSUIT_AUDITOR_FAST_MODEL"
        value = var.fast_model
      }
      env {
        name  = "PURSUIT_AUDITOR_REASONING_MODEL"
        value = var.reasoning_model
      }
      env {
        name  = "SESSION_BACKEND"
        value = "cloud_sql"
      }
      env {
        name  = "ENABLE_SECRET_MANAGER"
        value = "true"
      }
      env {
        name  = "ENABLE_CLOUD_DLP"
        value = "true"
      }
      env {
        name  = "DLP_DEIDENTIFY_TEMPLATE_ID"
        value = var.dlp_deidentify_template_id
      }
      env {
        name  = "ENABLE_MODEL_ARMOR"
        value = var.model_armor_template_id != "" ? "true" : "false"
      }
      env {
        name  = "MODEL_ARMOR_TEMPLATE_ID"
        value = var.model_armor_template_id
      }
      env {
        name  = "VERTEX_AI_SEARCH_DATASTORE_ID"
        value = var.vertex_ai_search_datastore_id
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.service_name
      }
      env {
        name  = "OTEL_EXPORTER_MODE"
        value = "gcp_trace"
      }
      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }

      # Secrets injected directly from Google Cloud Secret Manager (never plaintext)
      env {
        name = "SESSION_DB_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_db_url.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds = 30
      }
    }
  }

  depends_on = [
    google_project_service.enabled_apis,
    google_secret_manager_secret_iam_member.app_sa_db_url_secret_access,
    google_project_iam_member.app_sa_bindings,
  ]
}

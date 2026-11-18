# -----------------------------------------------------------------------------
# Google Cloud Secret Manager — Zero Hardcoded Credentials
# Populate secret versions out-of-band via `gcloud secrets versions add`.
# -----------------------------------------------------------------------------

resource "google_secret_manager_secret" "gemini_api_key" {
  project   = var.project_id
  secret_id = "pursuit-auditor-gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret" "session_db_url" {
  project   = var.project_id
  secret_id = "pursuit-auditor-session-db-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret_iam_member" "app_sa_gemini_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.gemini_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "app_sa_db_url_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.session_db_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

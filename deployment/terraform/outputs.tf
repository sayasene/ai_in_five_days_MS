output "cloud_run_service_url" {
  description = "HTTPS endpoint of the deployed Pursuit Deliverable Auditor Cloud Run service."
  value       = google_cloud_run_v2_service.pursuit_auditor.uri
}

output "runtime_service_account_email" {
  description = "Least-privilege runtime IAM service account email."
  value       = google_service_account.app_sa.email
}

output "cicd_service_account_email" {
  description = "Workload Identity Federation CI/CD service account email."
  value       = google_service_account.cicd_runner_sa.email
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL PostgreSQL connection string for persistent ADK sessions."
  value       = google_sql_database_instance.sessions_db.connection_name
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository path."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agent_images.repository_id}"
}

output "analytics_bigquery_dataset" {
  description = "BigQuery dataset receiving AgentOps audit and evaluation telemetry."
  value       = google_bigquery_dataset.agent_analytics.dataset_id
}

output "secret_ids" {
  description = "Google Cloud Secret Manager secret IDs managed by Terraform."
  value = [
    google_secret_manager_secret.gemini_api_key.secret_id,
    google_secret_manager_secret.session_db_url.secret_id,
  ]
}

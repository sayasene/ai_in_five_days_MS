# -----------------------------------------------------------------------------
# Observability Infrastructure: GCS Telemetry Bucket, BigQuery Analytics, Alerts
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "telemetry_logs" {
  name                        = "${var.project_id}-${var.service_name}-telemetry-${var.environment}"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_storage_bucket_iam_member" "app_sa_telemetry_writer" {
  bucket = google_storage_bucket.telemetry_logs.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_bigquery_dataset" "agent_analytics" {
  dataset_id                 = "${replace(var.service_name, "-", "_")}_analytics_${var.environment}"
  project                    = var.project_id
  location                   = var.region
  description                = "Structured AgentOps telemetry, intent-vs-outcome audit logs, and eval metrics."
  delete_contents_on_destroy = var.environment != "prod"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_monitoring_alert_policy" "cloud_run_error_rate" {
  project      = var.project_id
  display_name = "${var.service_name} (${var.environment}) — Cloud Run 5xx Spike"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx response count > 5 in 5m"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${google_cloud_run_v2_service.pursuit_auditor.name}\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 5

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

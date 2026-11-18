# -----------------------------------------------------------------------------
# Least-Privilege Runtime & CI/CD Service Accounts + GitHub WIF (Zero Static Keys)
# -----------------------------------------------------------------------------

resource "google_service_account" "app_sa" {
  account_id   = "${var.service_name}-sa"
  display_name = "Pursuit Deliverable Auditor Runtime Service Account"
  project      = var.project_id

  depends_on = [google_project_service.enabled_apis]
}

locals {
  app_sa_roles = toset([
    "roles/aiplatform.user",
    "roles/bigquery.dataEditor",
    "roles/cloudsql.client",
    "roles/cloudtrace.agent",
    "roles/dlp.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
}

resource "google_project_iam_member" "app_sa_bindings" {
  for_each = local.app_sa_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_service_account" "cicd_runner_sa" {
  account_id   = "${var.service_name}-cicd"
  display_name = "Pursuit Auditor GitHub Actions CI/CD Runner"
  project      = var.project_id

  depends_on = [google_project_service.enabled_apis]
}

locals {
  cicd_roles = toset([
    "roles/artifactregistry.writer",
    "roles/run.developer",
    "roles/iam.serviceAccountUser",
  ])
}

resource "google_project_iam_member" "cicd_runner_bindings" {
  for_each = local.cicd_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.cicd_runner_sa.email}"
}

# Workload Identity Federation pool for keyless GitHub Actions authentication
resource "google_iam_workload_identity_pool" "github_pool" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.service_name}-gh-pool"
  display_name              = "GitHub Actions Pool (${var.environment})"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub Actions OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_wif_impersonation" {
  service_account_id = google_service_account.cicd_runner_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repository}"
}

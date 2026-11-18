variable "project_id" {
  description = "Target Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud deployment region."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "service_name" {
  description = "Logical name prefix for Cloud Run, IAM, and storage resources."
  type        = string
  default     = "pursuit-auditor-agent"
}

variable "container_image" {
  description = "Full Artifact Registry container image URI deployed to Cloud Run."
  type        = string
  default     = "us-central1-docker.pkg.dev/your-gcp-project-id/pursuit-auditor-agent/app:latest"
}

variable "cloud_sql_tier" {
  description = "Cloud SQL PostgreSQL machine tier for persistent ADK sessions and pgvector."
  type        = string
  default     = "db-f1-micro"
}

variable "fast_model" {
  description = "Fast-tier Gemini model used for intake, Gate 3 visual audits, and event compaction."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "reasoning_model" {
  description = "Reasoning-tier Gemini model used for Gate 1 math, Gate 2 commercial/SOW, and Gate 4 Q&A."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "model_armor_template_id" {
  description = "Optional Google Cloud Model Armor template ID for prompt/response guardrails."
  type        = string
  default     = ""
}

variable "dlp_deidentify_template_id" {
  description = "Optional Google Cloud DLP de-identify template resource name for PII scrubbing."
  type        = string
  default     = ""
}

variable "vertex_ai_search_datastore_id" {
  description = "Optional Vertex AI Search datastore resource ID for enterprise precedent grounding."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "GitHub repository (owner/repo) authorized via Workload Identity Federation."
  type        = string
  default     = "sayasene/ai_in_five_days_MS"
}

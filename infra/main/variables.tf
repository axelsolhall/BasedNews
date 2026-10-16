variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "github_repo" {
  description = "owner/name of the repository allowed to deploy (OIDC)"
  type        = string
  default     = "axelsolhall/BasedNews"
}

variable "alert_email" {
  description = "failed runs and the kill switch email this address (confirm the subscription mail once)"
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "the tag every job runs; CI moves it to the newest commit on main"
  type        = string
  default     = "prod"
}

variable "task_cpu" {
  type    = number
  default = 512
}

variable "task_memory" {
  description = "The largest step is 2.3 story grouping: 1.3 GB on a 61 k-article window (2026-10-16, sparse average linkage, ADR-004); a full 41-country window is ≈ 170 k articles, so 4 GB — the most 0.5 vCPU allows"
  type        = number
  default     = 4096
}

# Schedules are created switched off: turning them on is a decision (docs/DEPLOY.md step 3).
variable "refresh_enabled" {
  description = "hourly ingest → normalize → embed → stories and the daily feed check (embeddings ≈ $0.01/day)"
  type        = bool
  default     = false
}

variable "judge_enabled" {
  description = "daily measure → export: judges the new story pairs (paid: spends up to daily_budget_usd)"
  type        = bool
  default     = false
}

variable "daily_budget_usd" {
  description = "AI budget per daily judge run (A48); the monthly cap in the app still applies"
  type        = string
  default     = "0.15"
}

variable "maintenance_enabled" {
  description = "daily retention with --apply (A52): archives to the lake, then deletes old text"
  type        = bool
  default     = false
}

variable "publish_positions" {
  description = "A54: upload the latest fit to the public site (labelled not validated)"
  type        = bool
  default     = false
}

variable "killswitch_usd" {
  description = "monthly AWS cost at which every schedule is switched off automatically"
  type        = number
  default     = 15
}

# Pub/Sub Topic and Subscription for Run Dispatch

resource "google_pubsub_topic" "runs_topic" {
  name = "studio-production-runs"
  labels = {
    app = "studio-production-commander"
  }
}

resource "google_pubsub_subscription" "runs_sub" {
  name  = "studio-production-runs-sub"
  topic = google_pubsub_topic.runs_topic.name

  ack_deadline_seconds = 60

  retry_policy {
    minimum_backoff = "5s"
    maximum_backoff = "60s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.runs_dlq.id
    max_delivery_attempts = 5
  }
}

resource "google_pubsub_topic" "runs_dlq" {
  name = "studio-production-runs-dlq"
}

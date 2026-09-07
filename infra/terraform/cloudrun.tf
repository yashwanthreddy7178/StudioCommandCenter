# Cloud Run Services for Studio Production Commander
#
# Every service below is pinned to a single instance, and that is a constraint
# of the code rather than a cost decision. The state these services keep is
# in-process, not shared:
#
#   api-gateway      tenant leases        (src/lease.py, a dict)
#   action-executor  approvals and the idempotency keys guarding them
#   mcp-gateway      response cache, singleflight, token-bucket rate limiter
#   render-sim       the 24 simulated tenant worlds themselves
#   impact-engine    SQLite, in-memory by default
#
# A second replica does not share any of it. Two instances would lease the same
# tenant to two sessions, execute one approved remediation twice because the
# idempotency key lives on the other instance, and rate-limit against Grafana at
# twice the intended ceiling. Raising these numbers means moving that state to
# Redis, Firestore and Postgres first -- the Redis and SQL resources alongside
# this file exist for exactly that, and the code does not use them yet.
#
# deploy/cloud-run-deploy.sh ships the whole stack as one container pinned the
# same way; this file describes the split topology.

# 1. API Gateway
resource "google_cloud_run_v2_service" "api_gateway" {
  name     = "api-gateway"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 80

    containers {
      image = "gcr.io/${var.project_id}/api-gateway:latest"
      ports {
        container_port = 8000
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
      }
    }
  }
}

# 2. Stream Service (SSE fan-out)
resource "google_cloud_run_v2_service" "stream_service" {
  name     = "stream-service"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 250

    containers {
      image = "gcr.io/${var.project_id}/stream-service:latest"
      ports {
        container_port = 8005
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
      }
    }
  }
}

# 3. Agent Worker (ADK + Gemini)
resource "google_cloud_run_v2_service" "agent_worker" {
  name     = "agent-worker"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 4

    containers {
      image = "gcr.io/${var.project_id}/agent-worker:latest"
      ports {
        container_port = 8010
      }
      resources {
        limits = {
          cpu    = "4"
          memory = "2Gi"
        }
      }
    }
  }
}

# 4. MCP Gateway
resource "google_cloud_run_v2_service" "mcp_gateway" {
  name     = "mcp-gateway"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 60

    containers {
      image = "gcr.io/${var.project_id}/mcp-gateway:latest"
      ports {
        container_port = 8001
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
      }
    }
  }
}

# 5. Deterministic Impact Engine
resource "google_cloud_run_v2_service" "impact_engine" {
  name     = "impact-engine"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 80

    containers {
      image = "gcr.io/${var.project_id}/impact-engine:latest"
      ports {
        container_port = 8002
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
      }
    }
  }
}

# 6. Action Executor
resource "google_cloud_run_v2_service" "action_executor" {
  name     = "action-executor"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 20

    containers {
      image = "gcr.io/${var.project_id}/action-executor:latest"
      ports {
        container_port = 8003
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }
}

# 7. Render Simulator
resource "google_cloud_run_v2_service" "render_sim" {
  name     = "render-sim"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    scaling {
      min_instance_count = 1 # Always warm
      max_instance_count = 1 # see the note at the top of this file
    }
    max_instance_request_concurrency = 80

    containers {
      image = "gcr.io/${var.project_id}/render-sim:latest"
      ports {
        container_port = 8004
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }
    }
  }
}

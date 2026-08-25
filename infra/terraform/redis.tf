# Memorystore Redis for MCP Cache, Locks, and Tenant Leases

resource "google_redis_instance" "cache" {
  name           = "studio-redis-cache"
  tier           = "BASIC"
  memory_size_gb = 2
  region         = var.region

  redis_version     = "REDIS_7_0"
  display_name      = "Studio Production Commander Redis Cache"
  authorized_network = "default"

  labels = {
    app = "studio-production-commander"
  }
}

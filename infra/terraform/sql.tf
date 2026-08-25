# Cloud SQL PostgreSQL for Production Metadata

resource "google_sql_database_instance" "postgres" {
  name             = "studio-production-postgres"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier = "db-f1-micro"

    ip_configuration {
      ipv4_enabled = true
    }

    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "database" {
  name     = "studio_production"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "users" {
  name     = "studio_user"
  instance = google_sql_database_instance.postgres.name
  password = "studio_secure_password"
}

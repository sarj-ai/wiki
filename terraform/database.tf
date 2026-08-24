
resource "google_sql_database" "sarj_wiki" {
  name     = "sarj_wiki"
  instance = google_sql_database_instance.dammam[0].name
}

resource "random_password" "wiki_db" {
  length  = 64
  special = false
}

resource "google_sql_user" "wiki_app" {
  name     = "sarj_wiki_app"
  instance = google_sql_database_instance.dammam[0].name
  password = random_password.wiki_db.result
}

resource "google_sql_user" "wiki_app_iam" {
  name     = trimsuffix(google_service_account.backend.email, ".gserviceaccount.com")
  instance = google_sql_database_instance.dammam[0].name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

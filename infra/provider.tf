provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "movierec"
      managed_by = "terraform"
    }
  }
}

# load the private values from .env
export $(grep -v '^#' .env | xargs)

# create some new variables based on the loaded values
export ZONE="${REGION}-${ZONE_LETTER}"
export CLUSTER="${CLUSTER_NAME}"
export SAGAN_IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}"
export FRONT_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-frontend"
export BACK_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-backend"

# apply settings
gcloud container clusters get-credentials ${CLUSTER} --zone ${ZONE} --project ${PROJECT_ID}
skaffold config set default-repo ${SAGAN_IMAGE_REPO}
kubectl config current-context
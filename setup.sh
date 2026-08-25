export $(grep -v '^#' .env | xargs)

export CLUSTER="${CLUSTER_NAME}"
export REGION="${REGION}"
export PROJECT_ID="${PROJECT_ID}"
export ZONE_LETTER="${ZONE_LETTER}"

zone="${REGION}-${ZONE_LETTER}"

export SAGAN_IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}"
export FRONT_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-frontend"
export BACK_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-backend"

gcloud container clusters get-credentials "${CLUSTER}" --zone "${zone}" --project "${PROJECT_ID}"
skaffold config set default-repo "${SAGAN_IMAGE_REPO}"
kubectl config current-context



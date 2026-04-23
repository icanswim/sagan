# sagan

A utility for serving containerized data science applications. 

# stack

skaffold for local dev
minikube for local dev
gke
    gcloud
    kubectl
docker  
uv  
fastapi  
streamlit  

## workflow

#environment

gcloud auth login
gcloud components install skaffold

#create setup.sh
    #load the private values from .env
    export $(grep -v '^#' .env | xargs)

    #create some new variables based on the loaded values
    export ZONE="${REGION}-${ZONE_LETTER}"
    export CLUSTER="${CLUSTER_NAME}"
    export SAGAN_IMAGE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}"
    export FRONT_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-frontend"
    export BACK_IMAGE_URI="${SAGAN_IMAGE_REPO}/sagan-backend"

    #apply settings
    gcloud container clusters get-credentials ${CLUSTER} --zone ${ZONE} --project ${PROJECT_ID}
    skaffold config set default-repo ${SAGAN_IMAGE_REPO}
    kubectl config current-context

source setup.sh
  
gcloud projects create "$PROJECT_ID"
gcloud config set project "$PROJECT_ID"

gcloud services enable compute.googleapis.com
gcloud compute addresses create sagan-ingress-ip --global  
gcloud compute addresses describe sagan-ingress-ip --global --format="get(address)"  
#create DNS redirect for app.wylderhayes.com to sagan-ingress-ip  

gcloud services enable artifactregistry.googleapis.com  
gcloud artifacts repositories create ${IMAGE_REPO_NAME} --repository-format=docker --location=${REGION} --description="sagan-app"  

#set IMAGE_URIs in deployment.yaml

#enable docker to work with gcloud
gcloud auth configure-docker ${REGION}-docker.pkg.dev  

gcloud certificate-manager dns-authorizations create sagan-dns-auth --domain="app.wylderhayes.com"
gcloud certificate-manager dns-authorizations list  

#update dns provider
gcloud certificate-manager dns-authorizations describe sagan-dns-auth
#create CNAME dns record
gcloud certificate-manager certificates create sagan-managed-cert \
    --domains='app.wylderhayes.com' \
    --dns-authorizations=sagan-dns-auth
gcloud certificate-manager certificates describe sagan-managed-cert

gcloud certificate-manager maps create sagan-cert-map
gcloud certificate-manager maps entries create sagan-map-entry \
    --map=sagan-cert-map \
    --hostname="app.wylderhayes.com" \
    --certificates=sagan-managed-cert
gcloud certificate-manager maps describe sagan-cert-map
gcloud certificate-manager maps entries list --map=sagan-cert-map

#check io
gcloud compute addresses list --global
gcloud certificate-manager maps list
gcloud certificate-manager maps entries list --map=sagan-cert-map
gcloud certificate-manager certificates list

gcloud services enable container.googleapis.com  

gcloud container clusters create ${CLUSTER} \
    --workload-pool=${PROJECT_ID}.svc.id.goog \
    --addons=GcsFuseCsiDriver \
    --gateway-api=standard \
    --zone=${ZONE} \
    --machine-type e2-medium \
    --num-nodes=1  

gcloud container clusters get-credentials ${CLUSTER} --zone ${ZONE} 

kubectl create namespace sagan-app --save-config

gcloud iam service-accounts create sagan-gsa \
    --display-name="sagan gke service account"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/container.defaultNodeServiceAccount"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader"

export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
gcloud storage buckets add-iam-policy-binding gs://${BUCKET_NAME} \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/storage.admin"

gcloud storage buckets add-iam-policy-binding gs://${BUCKET_NAME} \
    --member="serviceAccount:sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/storage.objectUser"

kubectl create serviceaccount sagan-backend-ksa -n sagan-app

gcloud iam service-accounts add-iam-policy-binding sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:${PROJECT_ID}.svc.id.goog[sagan-app/sagan-backend-ksa]"

#bucket creation

gcloud storage buckets create gs://${BUCKET_NAME} \
    --location=${REGION} \
    --uniform-bucket-level-access \
    --enable-hierarchical-namespace

kubectl get daemonset gcs-fuse-csi-driver -n kube-system
#create lifecycle.json for bucket maintainence
gcloud storage buckets update gs://${BUCKET_NAME} --lifecycle-file=lifecycle.json
gcloud storage buckets describe gs://${BUCKET_NAME} --format="json(lifecycle)"
kubectl label namespace sagan-app gke-gcsfuse-sidecar-injection=enabled

kubectl annotate serviceaccount sagan-backend-ksa \
    --namespace sagan-app \
    iam.gke.io/gcp-service-account=sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com

kubectl create serviceaccount sagan-frontend-ksa -n sagan-app

gcloud iam service-accounts add-iam-policy-binding sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:${PROJECT_ID}.svc.id.goog[sagan-app/sagan-frontend-ksa]"

kubectl annotate serviceaccount sagan-frontend-ksa \
    --namespace sagan-app \
    iam.gke.io/gcp-service-account=sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com

gcloud container node-pools create spot-frontend-pool \
    --cluster ${CLUSTER} \
    --spot \
    --zone us-central1-a \
    --machine-type e2-medium \
    --node-taints dedicated=spot:NoSchedule \
    --service-account=sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com \
    --num-nodes 1 \
    --workload-metadata=GKE_METADATA

gcloud container node-pools create spot-backend-pool \
    --cluster ${CLUSTER} \
    --spot \
    --zone us-central1-a \
    --machine-type e2-standard-2 \
    --disk-size 40 \
    --disk-type pd-balanced \
    --node-taints dedicated=spot:NoSchedule \
    --service-account=sagan-gsa@${PROJECT_ID}.iam.gserviceaccount.com \
    --num-nodes 1 \
    --workload-metadata=GKE_METADATA

gcloud container clusters describe ${CLUSTER} \
    --location ${ZONE} \
    --format="value(config.addonsConfig.gcsFuseCsiDriverConfig.enabled)"

#assemble repo  

mkdir app
cd app 
#create
    skaffold.yaml
mkdir k8s
cd k8s
#create     
    routes.yaml
    deployment.yaml
    gateway.yaml
    gcp-backend-policy.yaml
    istio-class.yaml
    job.yaml
    namespace.yaml
cd ..
mkdir local
cd local
#create
    k8s-rbac.yaml
    local-deployment.yaml
    local-gateway.yaml
    local-job.yaml
    pvc.yaml
uv init frontend  
uv add streamlit requests  
#create Dockerfile frontend
uv init backend  
uv add fastapi
#create Dockerfile backend

#start the gateway

gcloud compute networks subnets create sagan-proxy-subnet \
    --purpose=REGIONAL_MANAGED_PROXY \
    --role=ACTIVE \
    --region=${REGION} \
    --network=default \
    --range=172.16.0.0/23

gcloud compute firewall-rules create allow-gke-gw-frontend-hc \
    --network=default \
    --action=ALLOW \
    --direction=INGRESS \
    --source-ranges=130.211.0.0/22,35.191.0.0/16 \
    --rules=tcp:8501
kubectl apply -f gateway.yaml

#gateway api requires a secret in the certificateRefs section of gateway.yaml. 
#otherwise it throws the GWCER102 error.  creating a dummy secret 
#with the same name as your cert map satisfies the validator.  gke's 
#backend controller automatically swaps the dummy for the real sagan-cert-map.

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/tls.key -out /tmp/tls.crt \
  -subj "/CN=app.wylderhayes.com"

kubectl create secret tls sagan-cert-map \
  -n sagan-app \
  --cert=/tmp/tls.crt \
  --key=/tmp/tls.key

gcloud compute networks subnets list --filter="purpose=REGIONAL_MANAGED_PROXY AND region:us-central1"
gcloud compute networks list

#wait for gateway to be programmed
kubectl get gateway sagan-gateway -n sagan-app --watch 
kubectl describe gateway sagan-gateway -n sagan-app
kubectl apply -f . --dry-run=server

#run it 
#pipe BUCKET_NAME and any other variables from skaffold.yaml to skaffold run (since skaffold.yaml cant transmit bucket to googleCloudBuild)
envsubst < skaffold.yaml | skaffold run -p gke -f -

#check the negs (network endpoint groups)  
kubectl get gateway external-http-gateway -o=jsonpath="{.status.addresses[0].value}" --watch # get gateway ip
gcloud certificate-manager maps entries describe sagan-map-entry --map=sagan-cert-map
kubectl get svc frontend-service -o jsonpath='{.metadata.annotations["cloud\.google\.com/neg-status"]}' # describe negs

# running

#environment
source setup.sh

#pipe BUCKET_NAME and any other variables from skaffold.yaml to skaffold run (since skaffold.yaml cant transmit bucket to googleCloudBuild)
envsubst < skaffold.yaml | skaffold run -p gke -f -

gcloud compute networks subnets list --filter="purpose=REGIONAL_MANAGED_PROXY AND region:us-central1"
gcloud compute networks list
kubectl describe gateway sagan-gateway -n sagan-app

kubectl get crds
kubectl get services  
kubectl get pods  
kubectl get pods -n sagan-app -o wide -w
gcloud container clusters list
kubectl exec -it $(kubectl get pod -l app=backend -n sagan-app -o name) -n sagan-app -- ls /app/data


#restart/delete/idle

kubectl rollout restart deployment backend-deployment 
skaffold delete -p gke  # removes K8s resources
gcloud container clusters delete ${CLUSTER} --zone ${ZONE}  
gcloud container clusters resize ${CLUSTER} --zone ${ZONE} --node-pool spot-backend-pool --num-nodes 1
gcloud container clusters resize ${CLUSTER} --zone ${ZONE} --node-pool spot-frontend-pool --num-nodes 1


# local skaffold dev minikube

minikube start --cpus 4 --memory 8192
eval $(minikube docker-env) # set
docker build -t sagan-frontend ./app/frontend
docker build -t sagan-backend ./app/backend
minikube image ls

#one time install
curl -sL https://istio.io/downloadIstioctl | sh -
export PATH=$HOME/.istioctl/bin:$PATH 
istioctl install --set profile=demo -y

#clean up past deployment

kubectl config use-context minikube
skaffold delete
kubectl delete jobs,pods --all -n sagan-app
#sync minikube and docker
eval $(minikube docker-env)
#start the local deployment
skaffold dev --force=true --port-forward

#check the logs
kubectl get pods -n sagan-app
kubectl exec backend-deployment-5c76b9998d-4zhq2 -n sagan-app -- cat /app/data/train_job_20260409_164926.log
kubectl exec backend-deployment-5c76b9998d-4zhq2 -n sagan-app -- ls /app/data/



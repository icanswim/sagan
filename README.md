# sagan
A utility for serving containerized data science applications. 

# stack
gke  
docker  
uv  
fastapi  
streamlit  

## workflow

https://github.com/GoogleCloudPlatform/gke-networking-recipes

gcloud auth login  
create new gcloud project  
gcloud config set project PROJECT_ID  

export PROJECT_ID=$(gcloud config get-value project)  
export IMAGE_REPO_NAME=sagan-image-repo  
export IMAGE_TAG=sagan_engine
export FRONT_IMAGE_URI="us-central1-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}/sagan-frontend:${IMAGE_TAG}"  
export BACK_IMAGE_URI="us-central1-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}/sagan-backend:${IMAGE_TAG}" 

glcoud services enable compute.googleapis.com  
gcloud compute addresses create sagan-ingress-ip --global  
gcloud compute addresses describe sagan-ingress-ip --global --format="get(address)"  
create DNS redirect for app.wylderhayes.com to sagan-ingress-ip  

gcloud services enable artifactregistry.googleapis.com  
gcloud artifacts repositories create ${IMAGE_REPO_NAME} --repository-format=docker --location=us-central1 --description="sagan-app"  
set IMAGE_URI in deployment.yaml

gcloud auth configure-docker us-central1-docker.pkg.dev  

gcloud certificate-manager dns-authorizations create sagan-dns-auth --domain="app.wylderhayes.com"
gcloud certificate-manager dns-authorizations list  

update dns provider

gcloud certificate-manager dns-authorizations describe sagan-dns-auth
create CNAME dns record
gcloud certificate-manager certificates describe sagan-managed-cert

gcloud certificate-manager maps create sagan-cert-map
gcloud certificate-manager maps entries create sagan-map-entry \
    --map=sagan-cert-map \
    --hostname="app.wylderhayes.com" \
    --certificates=sagan-managed-cert
gcloud certificate-manager maps describe sagan-cert-map

check io

gcloud compute addresses list --global
gcloud certificate-manager maps list
gcloud certificate-manager maps entries list --map=sagan-cert-map
gcloud certificate-manager certificates list

assemble repo  

mkdir app  
create deployment.yaml  
create gateway.yaml  
create httproute.yaml  
uv init frontend  
uv add streamlit requests  
create Dockerfile frontend
uv init backend  
uv add fastapi
create Dockerfile backend

docker build -t ${FRONT_IMAGE_URI} ./app/frontend
docker build -t ${BACK_IMAGE_URI} ./app/backend

docker run -it --rm -p 8000:8000 --name backend-container ${BACK_IMAGE_URI} # local testing
docker run -it --rm -p 8501:8501 --name frontend-container ${FRONT_IMAGE_URI} # local testing

docker push ${FRONT_IMAGE_URI}  
docker push ${BACK_IMAGE_URI}

gcloud services enable container.googleapis.com  

gcloud container clusters create sagan-cluster \
    --zone=us-central1-a \
    --machine-type e2-medium \
    --num-nodes=1  

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a 

gcloud container clusters update sagan-cluster \
    --location=us-central1-a \
    --gateway-api=standard

kubectl create namespace sagan-app --save-config

gcloud iam service-accounts create sagan-gsa \
    --display-name="sagan gke service account"

gcloud projects add-iam-policy-binding sagan-5 \
    --member="serviceAccount:sagan-gsa@sagan-5.iam.gserviceaccount.com" \
    --role="roles/container.defaultNodeServiceAccount"

gcloud projects add-iam-policy-binding sagan-5 \
    --member="serviceAccount:sagan-gsa@sagan-5.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding sagan-5 \
    --member="serviceAccount:sagan-gsa@sagan-5.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader"

kubectl create serviceaccount sagan-backend-ksa -n sagan-app

gcloud iam service-accounts add-iam-policy-binding sagan-gsa@sagan-5.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:sagan-5.svc.id.goog[sagan-app/sagan-backend-ksa]"

kubectl annotate serviceaccount sagan-backend-ksa \
    --namespace sagan-app \
    iam.gke.io/gcp-service-account=sagan-gsa@sagan-5.iam.gserviceaccount.com

kubectl create serviceaccount sagan-frontend-ksa -n sagan-app

gcloud iam service-accounts add-iam-policy-binding sagan-gsa@sagan-5.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:sagan-5.svc.id.goog[sagan-app/sagan-frontend-ksa]"

kubectl annotate serviceaccount sagan-frontend-ksa \
    --namespace sagan-app \
    iam.gke.io/gcp-service-account=sagan-gsa@sagan-5.iam.gserviceaccount.com

gcloud container clusters update sagan-cluster \
    --location=us-central1-a \
    --workload-pool=sagan-5.svc.id.goog

gcloud container node-pools create spot-frontend-pool \
    --cluster sagan-cluster \
    --spot \
    --zone us-central1-a \
    --machine-type e2-medium \
    --node-taints dedicated=spot:NoSchedule \
    --service-account=sagan-gsa@sagan-5.iam.gserviceaccount.com \
    --num-nodes 1 \
    --workload-metadata=GKE_METADATA

gcloud container node-pools create spot-backend-pool \
    --cluster sagan-cluster \
    --spot \
    --zone us-central1-a \
    --machine-type e2-standard-2 \
    --disk-size 40 \
    --disk-type pd-balanced \
    --node-taints dedicated=spot:NoSchedule \
    --service-account=sagan-gsa@sagan-5.iam.gserviceaccount.com \
    --num-nodes 1 \
    --workload-metadata=GKE_METADATA

kubectl apply -f gateway.yaml

gateway api requires a secret in the certificateRefs section of gateway.yaml. 
otherwise it throws the GWCER102 error.  creating a dummy secret 
with the same name as your cert map satisfies the validator.  gke's 
backend controller automatically swaps the dummy for the real sagan-cert-map.

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/tls.key -out /tmp/tls.crt \
  -subj "/CN=app.wylderhayes.com"

kubectl create secret tls sagan-cert-map \
  -n sagan-app \
  --cert=/tmp/tls.crt \
  --key=/tmp/tls.key

gcloud compute networks subnets create sagan-proxy-subnet \
    --purpose=REGIONAL_MANAGED_PROXY \
    --role=ACTIVE \
    --region=us-central1 \
    --network=default \
    --range=172.16.0.0/23

gcloud compute firewall-rules create allow-gke-gw-frontend-hc \
    --network=default \
    --action=ALLOW \
    --direction=INGRESS \
    --source-ranges=130.211.0.0/22,35.191.0.0/16 \
    --rules=tcp:8501

kubectl get gateway sagan-gateway -n sagan-app --watch # wait for gateway to be programmed
kubectl describe gateway sagan-gateway -n sagan-app
kubectl apply -f routes.yaml
kubectl apply -f . --dry-run=server 

gcloud compute networks subnets list --filter="purpose=REGIONAL_MANAGED_PROXY AND region:us-central1"
gcloud compute networks list
kubectl describe gateway sagan-gateway -n sagan-app

kubectl get crds
kubectl get services  
kubectl get pods  
kubectl get pods -n sagan-app -o wide -w
gcloud container clusters list  
kubectl get gateway external-http-gateway -o=jsonpath="{.status.addresses[0].value}" --watch # get gateway ip
kubectl describe managedcertificate sagan-managed-cert 
kubectl get svc frontend-service -o jsonpath='{.metadata.annotations["cloud\.google\.com/neg-status"]}' # describe negs

kubectl rollout restart deployment sagan-deployment  
gcloud container clusters delete sagan-cluster --zone us-central1-a  

## instructions 



 


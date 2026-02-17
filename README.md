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
export IMAGE_TAG=v1 
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

create gateway

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

docker build -t ${FRONT_IMAGE_URI} ./frontend/Dockerfile  
docker build -t ${BACK_IMAGE_URI} ./backend/Dockerfile  

docker run -it --rm -p 8000:8000 --name backend-container backend # local testing
docker run -it --rm -p 8501:8501 --name frontend-container frontend # local testing

docker push ${FRONT_IMAGE_URI}  
docker push ${BACK_IMAGE_URI}

check io

gcloud compute addresses list --global
gcloud certificate-manager maps list
gcloud certificate-manager maps entries list --map=sagan-cert-map
gcloud certificate-manager certificates list

gcloud services enable container.googleapis.com  
gcloud container clusters create sagan-cluster --spot --zone=us-central1-a --num-nodes=2  

gcloud container node-pools create spot-pool \
    --cluster=sagan-cluster \
    --region=us-central1-a \
    --spot \
    --node-taints=cloud.google.com/gke-spot="true":NoSchedule

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a  

gcloud container clusters update sagan-cluster \
    --location=us-central1-a \
    --gateway-api=standard
kubectl get crd | grep gateway.networking.k8s.io
gcloud container clusters describe sagan-cluster \
    --location=us-central1-a \
    --format="json(networkConfig.gatewayApiConfig)"
    
kubectl create namespace sagan-app --save-config
kubectl apply -f gateway.yaml
kubectl get gateway sagan-gateway -n sagan-app --watch # wait for gateway to be programmed
kubectl apply -f routes.yaml
kubectl apply -f . --dry-run=server 

kubectl get crds
kubectl get services  
kubectl get pods  
gcloud container clusters list  
 
gcloud certificate-manager certificates describe sagan-managed-cert
kubectl get gateway external-http-gateway -o=jsonpath="{.status.addresses[0].value}" --watch # get gateway ip
kubectl describe managedcertificate sagan-managed-cert 
kubectl describe gateway sagan-gateway
kubectl get svc frontend-service -o jsonpath='{.metadata.annotations["cloud\.google\.com/neg-status"]}' # describe negs

kubectl rollout restart deployment sagan-deployment  
gcloud container clusters delete sagan-cluster --zone us-central1-a  

## instructions 



 


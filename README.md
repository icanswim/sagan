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
export IMAGE_NAME=sagan-image  
export IMAGE_TAG=test  
export IMAGE_URI="us-central1-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}/${IMAGE_NAME}:${IMAGE_TAG}"  

glcoud services enable compute.googleapis.com  
gcloud compute addresses create sagan-ingress-ip --global  
gcloud compute addresses describe sagan-ingress-ip --global --format="get(address)"  
create DNS redirect for app.wylderhayes.com to sagan-ingress-ip  

gcloud services enable artifactregistry.googleapis.com  
gcloud artifacts repositories create ${IMAGE_REPO_NAME} --repository-format=docker --location=us-central1 --description="gke docker fastapi"  
set IMAGE_URI in deployment.yaml

gcloud auth configure-docker us-central1-docker.pkg.dev  

docker build --pull --no-cache -t ${IMAGE_URI} .  
docker build -t ${IMAGE_URI} .  

docker build -f docker_frontend -t frontend . # for testing
docker build -f docker_backend -t backend . # for testing
docker run -it --rm -p 8000:8000 --name backend-container backend # for testing
docker run -it --rm -p 8501:8501 --name frontend-container frontend # for testing

docker push ${IMAGE_URI}  

create gateway

gcloud certificate-manager dns-authorizations create sagan-dns-auth --domain="app.wylderhayes.com"

update dns provider

gcloud certificate-manager dns-authorizations describe sagan-dns-auth
create CNAME dns record
gcloud certificate-manager certificates describe sagan-managed-cert

gcloud certificate-manager maps create sagan-cert-map
gcloud certificate-manager maps entries create sagan-map-entry \
    --map=sagan-cert-map \
    --hostname="app.wylderhayes.com" \
    --certificates=sagan-managed-cert

check ingress
gcloud compute addresses list --global
gcloud certificate-manager maps list
gcloud certificate-manager maps entries list --map=sagan-cert-map
gcloud certificate-manager certificates list

gcloud services enable container.googleapis.com  
gcloud container clusters create sagan-cluster --spot=True --machine-type=e2-medium --zone=us-central1-a --num-nodes=1  

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a  

assemble repo  
mkdir app  
create deployment.yaml  
create gateway.yaml  
create httproute.yaml  
create Dockerfile  
uv init frontend  
uv add streamlit requests  
uv init backend  
uv add fastapi  
 
kubectl apply -f .  

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



 


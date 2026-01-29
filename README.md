# sagan
A utility for serving containerized data science applications. 

# stack
gke  
docker  
uv  
fastapi  
streamlit  

## workflow

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

gcloud services enable container.googleapis.com  
gcloud container clusters create sagan-cluster --zone us-central1-a --num-nodes=1  

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a  

assemble repo  
mkdir app  
create deployment.yaml  
create ingress.yaml  
create frontend-config.yaml  
create managed-cert.yaml  
create Dockerfile  
uv init frontend  
uv add streamlit requests  
uv init backend  
uv add fastapi  
 
kubectl apply -f .  

kubectl get services  
kubectl get pods  
gcloud container clusters list  
kubectl describe managedcertificate sagan-managed-cert  
kubectl describe ingress sagan-ingress  

kubectl rollout restart deployment sagan-deployment  
gcloud container clusters delete sagan-cluster --zone us-central1-a  


## instructions 



 


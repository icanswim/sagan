# sagan
A utility for serving containerized data science applications. 

# stack

application stack  
cosmosis  

frontend stack  
uvicorn  
fastapi  

infrastructure stack  
github   
docker  
google artifacts  
gke  


## workflow

gcloud auth login  
create new gcloud project  
gcloud config set project PROJECT_ID  

export PROJECT_ID=$(gcloud config get-value project)  
export IMAGE_REPO_NAME=sagan-image-repo  
export IMAGE_NAME=sagan-image  
export IMAGE_TAG=test  
export IMAGE_URI="us-central1-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}/${IMAGE_NAME}:${IMAGE_TAG}"  

gcloud compute addresses create sagan-ingress-ip --global  
gcloud compute addresses describe sagan-ingress-ip --global --format="get(address)"  
create DNS redirect for app.wylderhayes.com to sagan-ingress-ip  

gcloud services enable artifactregistry.googleapis.com  
gcloud artifacts repositories create ${IMAGE_REPO_NAME} --repository-format=docker --location=us-central1 --description="gke docker fastapi"  

gcloud auth configure-docker us-central1-docker.pkg.dev  

docker build -t ${IMAGE_URI} .  
docker push ${IMAGE_URI}  

gcloud services enable container.googleapis.com  
gcloud container clusters create sagan-cluster --zone us-central1-a --num-nodes=1  

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a  

create deployment.yaml  
create ingress.yaml  
create frontend-config.yaml  
create managed-cert.yaml  

kubectl apply -f .

kubectl get service fastapi-service  
kubectl get deployments  
kubectl get services  
kubectl get pods  
gcloud container clusters list  
kubectl describe managedcertificate sagan-managed-cert  
kubectl describe ingress sagan-ingress  

gcloud container clusters delete sagan-cluster --zone us-central1-a  


## instructions 



 


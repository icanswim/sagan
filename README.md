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

export PROJECT_ID=$(gcloud config get-value project)  
export IMAGE_REPO_NAME=  
export IMAGE_NAME=  
export IMAGE_TAG=1.0  
export IMAGE_URI="us-central1-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO_NAME}/${IMAGE_NAME}:${IMAGE_TAG}"  

gcloud services enable artifactregistry.googleapis.com  
gcloud artifacts repositories create ${IMAGE_REPO_NAME} --repository-format=docker --location=us-central1 --description="docker image fastapi app"  

gcloud auth configure-docker us-central1-docker.pkg.dev  

docker build -t ${IMAGE_URI} .  
docker push ${IMAGE_URI}  

gcloud services enable container.googleapis.com  
gcloud container clusters create fastapi-cluster --zone us-central1-a --num-nodes=1  

gcloud container clusters get-credentials fastapi-cluster --zone us-central1-a  

create deployment.yaml from template  
kubectl apply -f deployment.yaml  

kubectl get service fastapi-service  
kubectl get deployments
kubectl get services
kubectl get pods

## instructions 



 


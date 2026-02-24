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
export IMAGE_TAG=v2 
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

docker run -it --rm -p 8000:8000 --name backend-container backend # local testing
docker run -it --rm -p 8501:8501 --name frontend-container frontend # local testing

docker push ${FRONT_IMAGE_URI}  
docker push ${BACK_IMAGE_URI}

gcloud services enable container.googleapis.com  
gcloud container clusters create sagan-cluster \
    --spot \
    --zone=us-central1-a \
    --num-nodes=2  

gcloud container node-pools create spot-pool \
    --cluster=sagan-cluster \
    --region=us-central1-a \
    --spot \
    --node-taints=cloud.google.com/gke-spot="true":NoSchedule

gcloud container clusters get-credentials sagan-cluster --zone us-central1-a  

gcloud container clusters update sagan-cluster \
    --location=us-central1-a \
    --gateway-api=standard

gcloud container clusters update sagan-cluster \
    --location=us-central1-a \
    --workload-pool=sagan-5.svc.id.goog

gcloud container node-pools update spot-pool \
    --cluster=sagan-cluster \
    --location=us-central1-a \
    --workload-metadata=GKE_METADATA

gcloud container node-pools update default-pool \
    --cluster=sagan-cluster \
    --location=us-central1-a \
    --workload-metadata=GKE_METADATA

kubectl get crd | grep gateway.networking.k8s.io
gcloud container clusters describe sagan-cluster \
    --location=us-central1-a \
    --format="json(networkConfig.gatewayApiConfig)"
    
kubectl create namespace sagan-app --save-config
kubectl apply -f gateway.yaml
kubectl get gateway sagan-gateway -n sagan-app --watch # wait for gateway to be programmed
kubectl describe gateway sagan-gateway -n sagan-app
kubectl apply -f routes.yaml
kubectl apply -f . --dry-run=server 

gcloud compute networks subnets list --filter="purpose=REGIONAL_MANAGED_PROXY AND region:us-central1"
gcloud compute networks list

gcloud compute networks subnets create sagan-proxy-subnet \
    --purpose=REGIONAL_MANAGED_PROXY \
    --role=ACTIVE \
    --region=us-central1 \
    --network=default \
    --range=172.16.0.0/23

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

gcloud compute firewall-rules create allow-gke-gw-frontend-hc \
    --network=default \
    --action=ALLOW \
    --direction=INGRESS \
    --source-ranges=130.211.0.0/22,35.191.0.0/16 \
    --rules=tcp:8501

kubectl describe gateway sagan-gateway -n sagan-app

kubectl get crds
kubectl get services  
kubectl get pods  
kubectl get pods -n sagan-app -o wide -w
gcloud container clusters list  

check the gateway 
kubectl get gateway external-http-gateway -o=jsonpath="{.status.addresses[0].value}" --watch # get gateway ip
kubectl describe managedcertificate sagan-managed-cert 
kubectl describe gateway sagan-gateway
kubectl get svc frontend-service -o jsonpath='{.metadata.annotations["cloud\.google\.com/neg-status"]}' # describe negs

kubectl create serviceaccount sagan-backend-ksa
gcloud iam service-accounts create sagan-backend-gsa \
    --display-name="Sagan Backend Service Account"
gcloud projects add-iam-policy-binding sagan-5 \
    --member="serviceAccount:sagan-backend-gsa@sagan-5.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
gcloud iam service-accounts add-iam-policy-binding sagan-backend-gsa@sagan-5.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:sagan-5.svc.id.goog[default/sagan-backend-ksa]"

kubectl create serviceaccount sagan-frontend-ksa -n sagan-app
gcloud iam service-accounts create sagan-frontend-gsa \
    --project=sagan-5 \
    --display-name="Sagan Frontend Service Account"
gcloud iam service-accounts add-iam-policy-binding sagan-frontend-gsa@sagan-5.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:sagan-5.svc.id.goog[sagan-app/sagan-frontend-ksa]"

kubectl annotate serviceaccount sagan-frontend-ksa \
    --namespace sagan-app \
    iam.gke.io/gcp-service-account=sagan-frontend-gsa@sagan-5.iam.gserviceaccount.com

kubectl patch deployment kube-dns -n kube-system -p \
'{"spec":{"template":{"spec":{"nodeSelector":{"cloud.google.com/gke-nodepool":"default-pool"}}}}}'

kubectl rollout restart deployment sagan-deployment  
gcloud container clusters delete sagan-cluster --zone us-central1-a  

## instructions 



 


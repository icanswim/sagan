import requests
import socket

def test_service(name, url):
    print(f"--- Testing {name} ---")
    # 1. Test DNS Resolution
    try:
        hostname = url.split("//")[-1].split(":")[0]
        ip = socket.gethostbyname(hostname)
        print(f"✅ DNS: {hostname} resolved to {ip}")
    except Exception as e:
        print(f"❌ DNS: Could not resolve {hostname}: {e}")
        return

    # 2. Test HTTP Connectivity
    try:
        response = requests.get(url, timeout=5)
        print(f"✅ HTTP: Connected to {url} (Status: {response.status_code})")
    except Exception as e:
        print(f"❌ HTTP: Failed to connect to {url}: {e}")

if __name__ == "__main__":
    # Test internal K8s DNS names
    test_service("Backend", "http://backend-service:8000/health")
    test_service("Frontend", "http://frontend-service:8501")


#Find your backend pod name:
#kubectl get pods -n sagan-app
#Copy the script into the pod:
#kubectl cp test_connection.py <BACKEND_POD_NAME>:/app/test_connection.py -n sagan-app
#Run it:
#kubectl exec -it <BACKEND_POD_NAME> -n sagan-app -- python /app/test_connection.py
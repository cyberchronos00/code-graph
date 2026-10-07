import os

from kubernetes import client, config


def run_export(name):
    config.load_incluster_config()
    batch = client.BatchV1Api()
    batch.create_namespaced_job("exports", {"metadata": {"name": name}})


def list_workers():
    config.load_incluster_config()
    return client.CoreV1Api().list_namespaced_pod(namespace="workers")


def scale(name):
    config.load_kube_config(config_file=os.environ["KUBECONFIG"])
    apps = client.AppsV1Api()
    apps.patch_namespaced_deployment(name, "shop", [])


def local_namespaces():
    config.load_kube_config(config_file="/home/ops/.kube/bookstore-config")
    return client.CoreV1Api().list_namespace()

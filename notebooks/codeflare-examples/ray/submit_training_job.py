from os import environ
from pprint import pprint
from time import sleep

from codeflare_sdk.cluster.cluster import Cluster, ClusterConfiguration
from codeflare_sdk.cluster.auth import TokenAuthentication
from codeflare_sdk.job.jobs import DDPJobDefinition


def submit_training_job(server_url='', token=''):
    
    server_url = environ.get('SERVER_URL', server_url)
    token = environ.get('TOKEN', token)
    
    auth = _cluster_login(server_url, token)
    cluster = _create_cluster()
    job = _submit_job(cluster)
    _wait_for_completion(job)
    _clean_up(cluster, auth)

    
def _cluster_login(server_url, token):
    print(f'logging into cluster at {server_url}')

    auth = TokenAuthentication(
        token=token, server=server_url, skip_tls=True
    )
    auth.login()

    print('login successful')
    return auth


def _create_cluster():
    print('creating framework cluster')

    cluster = Cluster(
        ClusterConfiguration(
            namespace='default',
            name='mnist-test',
            num_workers=2,
            min_cpus=2,
            max_cpus=2,
            min_memory=8,
            max_memory=8,
            num_gpus=0,
            instascale=False
        )
    )

    cluster.up()
    print('booting cluster')

    cluster.wait_ready()
    print('cluster is online')

    return cluster


def _submit_job(cluster):
    print('creating and submitting job')

    environment_variables = {
        'AWS_S3_ENDPOINT': environ.get('AWS_S3_ENDPOINT'),
        'AWS_ACCESS_KEY_ID': environ.get('AWS_ACCESS_KEY_ID'),
        'AWS_SECRET_ACCESS_KEY': environ.get('AWS_SECRET_ACCESS_KEY'),
        'AWS_S3_BUCKET': environ.get('AWS_S3_BUCKET'),
    }

    jobdef = DDPJobDefinition(
        name="mnisttest",
        script="mnist.py",
        scheduler_args={"requirements": "requirements.txt"},
        env=environment_variables,
    )
    job = jobdef.submit(cluster)

    print('job submitted')
    
    return job


def _wait_for_completion(job):
    print('awaiting job completion')

    job_finished = False
    while not job_finished:
        sleep(5)
        job_finished = job.status().is_terminal()
        print(f'job is finished: {job_finished}')

    print('job logs:\n')
    pprint(job.logs())

    return


def _clean_up(cluster, auth):
    print('shutting down cluster and logging out')

    cluster.down()
    auth.logout()

    print('cleanup complete')
    return


if __name__ == '__main__':
    submit_training_job()
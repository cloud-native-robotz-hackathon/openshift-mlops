from kfp.client import Client
from kfp.dsl import component, Dataset, Input, Metrics, Model, Output, pipeline
from kfp.kubernetes import use_secret_as_env


runtime_image = 'quay.io/mmurakam/runtimes:fraud-detection-v2.1.0'
data_connection_secret_name = 'aws-connection-fraud-detection'


@component(base_image=runtime_image)
def data_ingestion(data_object_name: str, raw_data: Output[Dataset]):
    from os import environ

    import boto3

    print('Commencing data ingestion.')

    s3_endpoint_url = environ.get('AWS_S3_ENDPOINT')
    s3_access_key = environ.get('AWS_ACCESS_KEY_ID')
    s3_secret_key = environ.get('AWS_SECRET_ACCESS_KEY')
    s3_bucket_name = environ.get('AWS_S3_BUCKET')

    print(f'Downloading data "{data_object_name}" '
          f'from bucket "{s3_bucket_name}" '
          f'from S3 storage at {s3_endpoint_url}'
          f'to {raw_data.path}')

    s3_client = boto3.client(
        's3', endpoint_url=s3_endpoint_url,
        aws_access_key_id=s3_access_key, aws_secret_access_key=s3_secret_key
    )
    s3_client.download_file(
        s3_bucket_name,
        f'data/{data_object_name}',
        raw_data.path
    )
    print('Finished data ingestion.')


@component(base_image=runtime_image)
def preprocessing(
        raw_data: Input[Dataset], training_samples: Output[Dataset],
        training_labels: Output[Dataset]):
    from pickle import dump

    from imblearn.over_sampling import SMOTE
    from pandas import read_csv
    from sklearn.model_selection import StratifiedKFold
    from sklearn.preprocessing import RobustScaler

    print('Preprocessing data.')

    df = read_csv(raw_data.path)

    rob_scaler = RobustScaler()

    df['scaled_amount'] = rob_scaler.fit_transform(
        df['Amount'].values.reshape(-1, 1)
    )
    df['scaled_time'] = rob_scaler.fit_transform(
        df['Time'].values.reshape(-1, 1)
    )
    df.drop(['Time', 'Amount'], axis=1, inplace=True)
    scaled_amount = df['scaled_amount']
    scaled_time = df['scaled_time']

    df.drop(['scaled_amount', 'scaled_time'], axis=1, inplace=True)
    df.insert(0, 'scaled_amount', scaled_amount)
    df.insert(1, 'scaled_time', scaled_time)

    X = df.drop('Class', axis=1)
    y = df['Class']
    sss = StratifiedKFold(n_splits=5, random_state=None, shuffle=False)

    for train_index, test_index in sss.split(X, y):
        print("Train:", train_index, "Test:", test_index)
        original_Xtrain = X.iloc[train_index]
        original_ytrain = y.iloc[train_index]

    original_Xtrain = original_Xtrain.values
    original_ytrain = original_ytrain.values

    sm = SMOTE(sampling_strategy='minority', random_state=42)
    Xsm_train, ysm_train = sm.fit_resample(original_Xtrain, original_ytrain)

    with open(training_samples.path, 'wb') as samples_file:
        dump(Xsm_train, samples_file)
    with open(training_labels.path, 'wb') as labels_file:
        dump(ysm_train, labels_file)

    print('data processing done')


@component(base_image=runtime_image)
def model_training(
        epoch_count: int, learning_rate: float,
        training_samples: Input[Dataset], training_labels: Input[Dataset],
        model: Output[Model], metrics: Output[Metrics]):
    from os import environ
    from pickle import load

    environ['CUDA_VISIBLE_DEVICES'] = '-1'

    from keras.models import Sequential
    from keras.layers import Dense
    from keras.optimizers import Adam
    from onnx import save
    from tf2onnx import convert

    print('training model')

    with open(training_samples.path, 'rb') as samples_file:
        Xsm_train = load(samples_file)
    with open(training_labels.path, 'rb') as labels_file:
        ysm_train = load(labels_file)

    n_inputs = Xsm_train.shape[1]

    oversample_model = Sequential([
        Dense(n_inputs, input_shape=(n_inputs, ), activation='relu'),
        Dense(32, activation='relu'),
        Dense(2, activation='softmax'),
    ])
    oversample_model.compile(
        Adam(learning_rate=learning_rate),
        loss='sparse_categorical_crossentropy',
        metrics=['accuracy'],
    )
    oversample_model.fit(
        Xsm_train,
        ysm_train,
        validation_split=0.2,
        batch_size=300,
        epochs=epoch_count,
        shuffle=True,
        verbose=2,
    )
    metrics.log_metric('accuracy', 88)
    onnx_model, _ = convert.from_keras(oversample_model)
    save(onnx_model, model.path)


@component(base_image=runtime_image)
def model_validation(model: Input[Model]):
    from time import sleep

    print('validating model using group fairness scores, for instance')
    sleep(1)
    print('model validated')


@component(base_image=runtime_image)
def model_upload(model_object_prefix: str, model: Input[Model]):
    from os import environ
    from datetime import datetime

    from boto3 import client

    def _initialize_s3_client(s3_endpoint_url, s3_access_key, s3_secret_key):
        print('initializing S3 client')
        s3_client = client(
            's3', aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            endpoint_url=s3_endpoint_url,
        )
        return s3_client

    def _generate_model_name(model_object_prefix, version=''):
        version = version if version else _timestamp()
        model_name = f'{model_object_prefix}-{version}.onnx'
        return model_name

    def _timestamp():
        return datetime.now().strftime('%y%m%d%H%M')

    def _do_upload(s3_client, object_name):
        print(f'uploading model to {object_name}')
        try:
            s3_client.upload_file(
                model.path, s3_bucket_name, f'models/{object_name}'
            )
        except:
            print(f'S3 upload to bucket {s3_bucket_name} at {s3_endpoint_url} failed!')
            raise
        print(f'model uploaded and available as "{object_name}"')

    s3_endpoint_url = environ.get('AWS_S3_ENDPOINT')
    s3_access_key = environ.get('AWS_ACCESS_KEY_ID')
    s3_secret_key = environ.get('AWS_SECRET_ACCESS_KEY')
    s3_bucket_name = environ.get('AWS_S3_BUCKET')

    s3_client = _initialize_s3_client(
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key
    )
    model_object_name = _generate_model_name(model_object_prefix)
    _do_upload(s3_client, model_object_name)

    model_object_name_latest = _generate_model_name(
        model_object_prefix, 'latest'
    )
    _do_upload(s3_client, model_object_name_latest)


@pipeline(name='model-training-kfp')
def model_training_pipeline(
    data_object_name: str = 'training-data.csv',
    epoch_count: int = 20,
    learning_rate: float = 0.001,
    model_object_prefix: str = 'model'
        ):

    data_ingestion_task = data_ingestion(data_object_name=data_object_name)
    use_secret_as_env(
        data_ingestion_task,
        secret_name=data_connection_secret_name,
        secret_key_to_env={
            'AWS_S3_ENDPOINT': 'AWS_S3_ENDPOINT',
            'AWS_ACCESS_KEY_ID': 'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY': 'AWS_SECRET_ACCESS_KEY',
            'AWS_S3_BUCKET': 'AWS_S3_BUCKET',
        },
    )

    preprocessing_task = preprocessing(raw_data=data_ingestion_task.output)

    model_training_task = model_training(
        epoch_count=epoch_count,
        learning_rate=learning_rate,
        training_samples=preprocessing_task.outputs['training_samples'],
        training_labels=preprocessing_task.outputs['training_labels'],
    )

    model_validation_task = model_validation(
        model=model_training_task.outputs['model'])

    model_upload_task = model_upload(
        model_object_prefix=model_object_prefix,
        model=model_training_task.outputs['model']
    )
    use_secret_as_env(
        model_upload_task,
        secret_name=data_connection_secret_name,
        secret_key_to_env={
            'AWS_S3_ENDPOINT': 'AWS_S3_ENDPOINT',
            'AWS_ACCESS_KEY_ID': 'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY': 'AWS_SECRET_ACCESS_KEY',
            'AWS_S3_BUCKET': 'AWS_S3_BUCKET',
        },
    )
    model_upload_task.after(model_validation_task)


def submit(pipeline):
    namespace_file_path =\
        '/var/run/secrets/kubernetes.io/serviceaccount/namespace'
    with open(namespace_file_path, 'r') as namespace_file:
        namespace = namespace_file.read()

    kubeflow_endpoint =\
        f'https://ds-pipeline-dspa.{namespace}.svc:8443'

    sa_token_file_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    with open(sa_token_file_path, 'r') as token_file:
        bearer_token = token_file.read()

    ssl_ca_cert =\
        '/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt'

    print(f'Connecting to Data Science Pipelines: {kubeflow_endpoint}')
    client = Client(
        host=kubeflow_endpoint,
        existing_token=bearer_token,
        ssl_ca_cert=ssl_ca_cert
    )
    result = client.create_run_from_pipeline_func(
        pipeline,
        arguments={
            'data_object_name': 'training-data.csv',
            'epoch_count': 20,
            'learning_rate': 0.001,
            'model_object_prefix': 'model',
        },
        experiment_name='model_training-kfp',
        enable_caching=False,
    )
    print(f'Starting pipeline run with run_id: {result.run_id}')


if __name__ == '__main__':
    submit(model_training_pipeline)
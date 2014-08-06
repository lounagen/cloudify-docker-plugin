import re

import docker

from cloudify import exceptions


_ERR_MSG_UNKNOWN_IMAGE_IMPORT = 'Unknown error during image import'
_ERR_MSG_UNKNOWN_IMAGE_BUILD = 'Unknown error while building image'
_NON_ENV_KEYS = [
    'daemon_client',
    'image_import',
    'image_build',
    'container_create',
    'container_start',
    'container_stop',
    'container_remove'
]


def _is_image_id_valid(ctx, image_id):
    return re.match('^[a-f0-9]{12,64}$', image_id) is not None


def _get_import_image_id(ctx, client, import_image_output):
    # import_image returns long string where in the second line
    # after last status is image id
    try:
        output_line = import_image_output.split('\n')[-2]
    except IndexError as e:
        _log_and_raise(ctx, client, _ERR_MSG_UNKNOWN_IMAGE_IMPORT)
    position_of_last_status = output_line.rfind('status')
    if position_of_last_status < 0:
        # If there was an error, there is no 'status'
        # in second output line
        # error message is after last 'error'
        position_of_error = output_line.rfind('error')
        err_msg = output_line[position_of_error:].split('"')[2]
        err_msg = 'Error during image import {}'.format(err_msg)
        _log_and_raise(ctx, client, err_msg)
    try:
        image_id = output_line[position_of_last_status:].split('"')[2]
    except IndexError as e:
        _log_and_raise(ctx, client, _ERR_MSG_UNKNOWN_IMAGE_IMPORT)
    if _is_image_id_valid(ctx, image_id):
        return image_id
    else:
        _log_and_raise(ctx, client, _ERR_MSG_UNKNOWN_IMAGE_IMPORT)


def _get_build_image_id(ctx, client, stream_generator):
    stream = None
    for stream in stream_generator:
        pass
    # Fourth word in a string stream is an id
    # I can't find it in docker documentation
    if stream is None:
        _log_and_raise(
            ctx,
            client,
            _ERR_MSG_UNKNOWN_IMAGE_BUILD,
            exceptions.RecoverableError
        )
    image_id = re.sub(r'[\W_]+', ' ', stream).split()[3]
    if _is_image_id_valid(ctx, image_id):
        return image_id
    else:
        _log_and_raise(
            ctx,
            client,
            _ERR_MSG_UNKNOWN_IMAGE_BUILD
        )


def import_image(ctx, client):
    ctx.logger.info('Importing image')
    image_id = _get_import_image_id(
        ctx,
        client,
        client.import_image(**ctx.properties['image_import'])
    )
    ctx.logger.info('Image {} has been imported'.format(image_id))
    return image_id


def build_image(ctx, client):
    ctx.logger.info(
        'Building image from path {}'.format(
            ctx.properties['image_build']['path']
        )
    )
    try:
        image_stream = client.build(**ctx.properties['image_build'])
    except OSError as e:
        error_msg = 'Error while building image: {}'.format(str(e))
        _log_and_raise(ctx, client, error_msg)
    else:
        image_id = _get_build_image_id(ctx, client, image_stream)
        ctx.logger.info('Image {} has been built'.format(image_id))
        return image_id


def get_client(ctx):
    daemon_client = ctx.properties.get('daemon_client', {})
    try:
        return docker.Client(**daemon_client)
    except docker.errors.DockerException as e:
        error_msg = 'Error while getting client: {}'.format(str(e))
        _log_and_raise(ctx, client, error_msg)


def create_container(ctx, client):
    ctx.logger.info('Creating container')
    container_create = ctx.properties.get('container_create', {})
    image = _get_image_or_raise(ctx, client)
    try:
        cont = client.create_container(image, **container_create)
    except docker.errors.APIError as e:
        error_msg = 'Error while creating container: {}'.format(str(e))
        _log_and_raise(ctx, client, error_msg)
    else:
        ctx.runtime_properties['container'] = cont['Id']
    _log_container_info(ctx, 'Created container ')


def start_container(ctx, client):
    _log_container_info(ctx, 'Starting container')
    container_start = ctx.properties.get('container_start', {})
    container = _get_container_or_raise(ctx, client)
    try:
        client.start(container, **container_start)
    except docker.errors.APIError as e:
        _log_and_raise(ctx, client, str(e))
    _log_container_info(ctx, 'Started container')


def stop_container(ctx, client):
    _log_container_info(ctx, 'Stopping container')
    container_stop = ctx.properties.get('container_stop', {})
    container = _get_container_or_raise(ctx, client)
    try:
        client.stop(container, **container_stop)
    except docker.errors.APIError as e:
        _log_and_raise(ctx, client, str(e))
    _log_container_info(ctx, 'Stopped container')


def remove_container(ctx, client):
    container = _get_container_or_raise(ctx, client)
    ctx.logger.info('Removing container {}'.format(container))
    container_remove = ctx.properties.get('container_remove', {})
    try:
        client.remove_container(container, **container_remove)
    except docker.errors.APIError as e:
        _log_and_raise(ctx, client, str(e))
    ctx.logger.info('Removed container {}'.format(container))


def remove_image(ctx, client):
    image = ctx.runtime_properties['image']
    _log_container_info(ctx, 'Removing image {}, container:'.format(image))
    try:
        client.remove_image(image)
    except docker.errors.APIError as e:
        _log_and_raise(ctx, client, str(e))
    _log_container_info(ctx, 'Removed image {}, container:'.format(image))


def set_env_var(ctx, client):
    if 'environment' not in ctx.properties['container_create']:
        ctx.properties['container_create']['environment'] = {}
    for key in ctx.properties:
        if (
            key not in _NON_ENV_KEYS and
            key not in ctx.properties['container_create']['environment']
        ):
            try:
                env_key = str(key)
                env_val = str(ctx.properties[key])
            except TypeError:
                pass
            else:
                ctx.properties['container_create']['environment'][env_key] =\
                    env_val


def get_top_info(ctx, client):

    def top_table(ctx, top_dict):
        top_table = ' '.join(top_dict['Titles']) + '\n'
        top_table += '\n'.join(' '.join(p) for p in top_dict['Processes'])
        return top_table

    _log_container_info(ctx, 'getting TOP info of container')
    container = _get_container_or_raise(ctx, client)
    try:
        top_dict = client.top(container)
    except docker.errors.APIError as e:
        _log_and_raise(ctx, client, str(e))
    else:
        return top_table(ctx, top_dict)


def get_container_info(ctx,  client):
    container = ctx.runtime_properties.get('container')
    if container is not None:
        for c in client.containers():
            if container in c.itervalues():
                return c
    return None


def inspect_container(ctx, client):
    container = ctx.runtime_properties.get('container')
    if container is not None:
        return client.inspect_container(container)
    return None


def _get_container_or_raise(ctx, client):
    container = ctx.runtime_properties.get('container')
    if container is None:
        _log_and_raise(ctx, client, 'No container specified')
    return container


def _get_image_or_raise(ctx, client):
    image = ctx.runtime_properties.get('image')
    if image is None:
        _log_and_raise(ctx, client, 'No image specified')
    return image


def _log_container_info(ctx, message=''):
    if 'container' in ctx.runtime_properties:
        message += ' ' + ctx.runtime_properties['container']
    ctx.logger.info(message)


def _log_and_raise(ctx,
                   client,
                   err_msg='',
                   exc_class=exceptions.NonRecoverableError):
    _log_error_container_logs(ctx, client, err_msg)
    raise exc_class(err_msg)


def _log_error_container_logs(ctx, client, message=''):
    container = ctx.runtime_properties.get('container')
    if container is not None:
        if message:
            message += '\n'
        message += 'Container: {}'.format(container)
        logs = client.logs(container)
        if logs:
            message += '\nLogs:\n{}'.format(logs)
    if message:
        ctx.logger.error(message)

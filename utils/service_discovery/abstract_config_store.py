# std
import logging
import simplejson as json
from os import path

# 3p
from urllib3.exceptions import TimeoutError

# project
from utils.checkfiles import get_check_class, get_auto_conf, get_auto_conf_images
from utils.singleton import Singleton

log = logging.getLogger(__name__)

CONFIG_FROM_AUTOCONF = 'auto-configuration'
CONFIG_FROM_TEMPLATE = 'template'
SD_TEMPLATE_DIR = '/datadog/check_configs'


class KeyNotFound(Exception):
    pass


class AbstractConfigStore(object):
    """Singleton for config stores"""
    __metaclass__ = Singleton

    previous_config_index = None

    def __init__(self, agentConfig):
        self.client = None
        self.agentConfig = agentConfig
        self.settings = self._extract_settings(agentConfig)
        self.client = self.get_client()
        self.sd_template_dir = agentConfig.get('sd_template_dir')
        self.AUTO_CONF_IMAGES = get_auto_conf_images(agentConfig)

    @classmethod
    def _drop(cls):
        """Drop the config store instance"""
        del cls._instances[cls]

    def _extract_settings(self, config):
        raise NotImplementedError()

    def get_client(self, reset=False):
        raise NotImplementedError()

    def client_read(self, path, **kwargs):
        raise NotImplementedError()

    def dump_directory(self, path, **kwargs):
        raise NotImplementedError()

    def _get_auto_config(self, image_name):
        if image_name in self.AUTO_CONF_IMAGES:
            check_name = self.AUTO_CONF_IMAGES[image_name]

            # get the check class to verify it matches
            check = get_check_class(self.agentConfig, check_name)
            if check is None:
                log.info("Could not find an auto configuration template for %s."
                         " Leaving it unconfigured." % image_name)
                return None

            auto_conf = get_auto_conf(self.agentConfig, check_name)
            init_config, instances = auto_conf.get('init_config'), auto_conf.get('instances')

            # stringify the dict to be consistent with what comes from the config stores
            init_config_tpl = json.dumps(init_config) if init_config else '{}'
            instance_tpl = json.dumps(instances[0]) if instances and len(instances) > 0 else '{}'

            return (check_name, init_config_tpl, instance_tpl)
        return None

    def get_check_tpls(self, image, **kwargs):
        """Retrieve template configs for an image from the config_store or auto configuration."""
        # TODO: make mixing both sources possible
        templates = []
        trace_config = kwargs.get('trace_config', False)

        # this flag is used when no valid configuration store was provided
        # it makes the method skip directly to the auto_conf
        if kwargs.get('auto_conf') is True:
            auto_config = self._get_auto_config(image)
            if auto_config is not None:
                source = CONFIG_FROM_AUTOCONF
                if trace_config:
                    return [(source, auto_config)]
                return [auto_config]
            else:
                log.debug('No auto config was found for image %s, leaving it alone.' % image)
                return None
        else:
            try:
                # Try to read from the user-supplied config
                check_names = self.client_read(path.join(self.sd_template_dir, image, 'check_names').lstrip('/'))
                init_config_tpls = self.client_read(path.join(self.sd_template_dir, image, 'init_configs').lstrip('/'))
                instance_tpls = self.client_read(path.join(self.sd_template_dir, image, 'instances').lstrip('/'))
                source = CONFIG_FROM_TEMPLATE
            except (KeyNotFound, TimeoutError):
                # If it failed, try to read from auto-config templates
                log.info("Could not find directory {0} in the config store, "
                         "trying to auto-configure the check...".format(image))
                auto_config = self._get_auto_config(image)
                if auto_config is not None:
                    source = CONFIG_FROM_AUTOCONF
                    # create list-format config based on an autoconf template
                    check_names, init_config_tpls, instance_tpls = map(lambda x: [x], auto_config)
                else:
                    log.debug('No auto config was found for image %s, leaving it alone.' % image)
                    return None
            except Exception:
                log.error(
                    'Fetching the value for {0} in the config store failed, '
                    'this image will not be configured by the service discovery.'.format(image))
                return None

        if len(check_names) != len(init_config_tpls) or len(check_names) != len(instance_tpls):
            log.error('Malformed configuration template: check_names, init_configs '
                      'and instances are not all the same length. Image {0} '
                      ' will not be configured by the service discovery'.format(image))
            return None

        for idx, c_name in enumerate(check_names):
            if trace_config:
                templates.append((source, (c_name, init_config_tpls[idx], instance_tpls[idx])))
            else:
                templates.append((c_name, init_config_tpls[idx], instance_tpls[idx]))
        return templates

    def crawl_config_template(self):
        """Return whether or not configuration templates have changed since the previous crawl"""
        try:
            config_index = self.client_read(self.sd_template_dir.lstrip('/'), recursive=True, watch=True)
        except KeyNotFound:
            log.debug('Config template not found (normal if running on auto-config alone).'
                      ' Not Triggering a config reload.')
            return False
        except TimeoutError:
            msg = 'Request for the configuration template timed out.'
            raise Exception(msg)
        # Initialize the config index reference
        if self.previous_config_index is None:
            self.previous_config_index = config_index
            return False
        # Config has been modified since last crawl
        if config_index != self.previous_config_index:
            log.info('Detected an update in config template, reloading check configs...')
            self.previous_config_index = config_index
            return True
        return False

    @staticmethod
    def extract_sd_config(config):
        """Extract configuration about service discovery for the agent"""
        sd_config = {}
        if config.has_option('Main', 'sd_config_backend'):
            sd_config['sd_config_backend'] = config.get('Main', 'sd_config_backend')
        else:
            sd_config['sd_config_backend'] = None
        if config.has_option('Main', 'sd_template_dir'):
            sd_config['sd_template_dir'] = config.get(
                'Main', 'sd_template_dir')
        else:
            sd_config['sd_template_dir'] = SD_TEMPLATE_DIR
        if config.has_option('Main', 'sd_backend_host'):
            sd_config['sd_backend_host'] = config.get(
                'Main', 'sd_backend_host')
        if config.has_option('Main', 'sd_backend_port'):
            sd_config['sd_backend_port'] = config.get(
                'Main', 'sd_backend_port')
        return sd_config


class StubStore(AbstractConfigStore):
    """Used when no valid config store was found. Allow to use auto_config."""
    def _extract_settings(self, config):
        pass

    def get_client(self):
        pass

    def crawl_config_template(self):
        # There is no user provided templates in auto_config mode
        return False

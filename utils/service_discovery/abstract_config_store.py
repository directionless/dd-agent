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
CONFIG_FROM_FILE = 'YAML file'
CONFIG_FROM_TEMPLATE = 'template'
TRACE_CONFIG = 'trace_config'  # used for tracing config load by service discovery


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
        self.auto_conf_images = get_auto_conf_images(agentConfig)

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
        if image_name in self.auto_conf_images:
            check_name = self.auto_conf_images[image_name]

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

    def get_check_tpl(self, image, **kwargs):
        """Retrieve template config strings from the ConfigStore."""
        # this flag is used when no valid configuration store was provided
        trace_config = kwargs.get(TRACE_CONFIG, False)
        source = ''

        if kwargs.get('auto_conf') is True:
            auto_config = self._get_auto_config(image)
            if auto_config is not None:
                check_name, init_config_tpl, instance_tpl = auto_config
                source = CONFIG_FROM_AUTOCONF
            else:
                log.debug('No auto config was found for image %s, leaving it alone.' % image)
                return None
        else:
            try:
                # Try to read from the user-supplied config
                check_name = self.client_read(path.join(self.sd_template_dir, image, 'check_name').lstrip('/'))
                init_config_tpl = self.client_read(path.join(self.sd_template_dir, image, 'init_config').lstrip('/'))
                instance_tpl = self.client_read(path.join(self.sd_template_dir, image, 'instance').lstrip('/'))
                source = CONFIG_FROM_TEMPLATE
            except (KeyNotFound, TimeoutError):
                # If it failed, try to read from auto-config templates
                log.info("Could not find directory {0} in the config store, "
                         "trying to auto-configure the check...".format(image))
                auto_config = self._get_auto_config(image)
                if auto_config is not None:
                    source = CONFIG_FROM_AUTOCONF
                    check_name, init_config_tpl, instance_tpl = auto_config
                else:
                    log.debug('No auto config was found for image %s, leaving it alone.' % image)
                    return None
            except Exception:
                log.warning(
                    'Fetching the value for {0} in the config store failed, '
                    'this check will not be configured by the service discovery.'.format(image))
                return None
        if trace_config:
            template = (source, (check_name, init_config_tpl, instance_tpl))
        else:
            template = (check_name, init_config_tpl, instance_tpl)
        return template

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


class StubStore(AbstractConfigStore):
    """Used when no valid config store was found. Allow to use auto_config."""
    def _extract_settings(self, config):
        pass

    def get_client(self):
        pass

    def crawl_config_template(self):
        # There is no user provided templates in auto_config mode
        return False

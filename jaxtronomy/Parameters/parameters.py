import copy
import numpy as np
import jax.numpy as jnp
from jax import lax


__all__ = ['Parameters']


class Parameters(object):
    """Class that manages parameters in JAX / auto-differentiable framework.
    Currently, it handles:
    - conversions from the differentiable parameter vector to user-friendly dictionnaries (args-to-kwargs, kwargs-to-args)
    - uniform and gaussian priors for subsets of parameters
    - log-prior values, that are meant to be added to the full loss function
    - nice LaTeX format for parameter names
    """

    _unif_prior_penalty = 1e10

    def __init__(self, lens_image, kwargs_init, kwargs_prior, kwargs_fixed):
        self._image = lens_image
        self._kwargs_init  = kwargs_init
        self._kwargs_prior = kwargs_prior
        self._kwargs_fixed = kwargs_fixed
        self._update_arrays()

        # TODO: write function that checks that no fields are missing
        # and fill those with default values if needed

    @property
    def optimized(self):
        return hasattr(self, '_kwargs_map')

    @property
    def num_parameters(self):
        return self._num_params

    @property
    def prior_types(self):
        return self._prior_types

    @property
    def bounds(self):
        return self._lowers, self._uppers

    @property
    def names(self):
        if not hasattr(self, '_names'):
            self._names = self._set_names('lens_model_list', 'kwargs_lens')
            self._names += self._set_names('source_model_list', 'kwargs_source')
            self._names += self._set_names('lens_light_model_list', 'kwargs_lens_light')
        return self._names

    @property
    def symbols(self):
        if not hasattr(self, '_symbols'):
            self._symbols = self._name2latex(self.names)
        return self._symbols

    @property
    def kwargs_model(self):
        # TODO: intermediate step, this might be suppressed in the future
        if not hasattr(self, '_kwargs_model'):
            self._kwargs_model = dict(lens_model_list=self._image.LensModel.lens_model_list,
                                      source_model_list=self._image.SourceModel.profile_type_list,
                                      lens_light_model_list=self._image.LensLightModel.profile_type_list)
        return self._kwargs_model

    def initial_values(self, as_kwargs=False):
        return self._kwargs_init if as_kwargs else self._init_values

    def current_values(self, as_kwargs=False, restart=False):
        if restart is True or not self.optimized:
            return self.initial_values(as_kwargs=as_kwargs)
        return self.best_fit_values(as_kwargs=as_kwargs)

    def best_fit_values(self, as_kwargs=False):
        """Maximum-a-postriori estimate"""
        return self._kwargs_map if as_kwargs else self._map_values

    def set_best_fit(self, args):
        self._map_values = args
        self._kwargs_map = self.args2kwargs(self._map_values)
    
    def set_posterior(self, samples):
        self._map_values = np.median(samples, axis=0)
        self._kwargs_map = self.args2kwargs(self._map_values)

    def update_fixed(self, kwargs_fixed):
        # TODO: fill current and init values with values that were previously fixed, if needed
        self._set_params_update_fixed(kwargs_fixed, 'lens_model_list', 'kwargs_lens')
        self._set_params_update_fixed(kwargs_fixed, 'source_model_list', 'kwargs_source')
        self._set_params_update_fixed(kwargs_fixed, 'lens_light_model_list', 'kwargs_lens_light')

        # update fixed settings and everything that depends on it
        self._kwargs_fixed.update(kwargs_fixed)
        self._update_arrays()

    def args2kwargs(self, args):
        i = 0
        args = jnp.atleast_1d(args)
        kwargs_lens, i = self._get_params(args, i, 'lens_model_list', 'kwargs_lens')
        kwargs_source, i = self._get_params(args, i, 'source_model_list', 'kwargs_source')
        kwargs_lens_light, i = self._get_params(args, i, 'lens_light_model_list', 'kwargs_lens_light')
        kwargs = {'kwargs_lens': kwargs_lens, 'kwargs_source': kwargs_source, 'kwargs_lens_light': kwargs_lens_light}
        return kwargs

    def kwargs2args(self, kwargs):
        args = self._set_params(kwargs, 'lens_model_list', 'kwargs_lens')
        args += self._set_params(kwargs, 'source_model_list', 'kwargs_source')
        args += self._set_params(kwargs, 'lens_light_model_list', 'kwargs_lens_light')
        return jnp.array(args)

    def kwargs2args_prior(self, kwargs_prior):
        types_m, lowers_m, uppers_m, means_m, widths_m = self._set_params_prior(kwargs_prior, 'lens_model_list', 'kwargs_lens')
        types_s, lowers_s, uppers_s, means_s, widths_s = self._set_params_prior(kwargs_prior, 'source_model_list', 'kwargs_source')
        types_l, lowers_l, uppers_l, means_l, widths_l = self._set_params_prior(kwargs_prior, 'lens_light_model_list', 'kwargs_lens_light')
        types =  types_m  + types_s  + types_l
        lowers = lowers_m + lowers_s + lowers_l
        uppers = uppers_m + uppers_s + uppers_l
        means  = means_m  + means_s  + means_l
        widths = widths_m + widths_s + widths_l
        return types, np.array(lowers), np.array(uppers), np.array(means), np.array(widths)

    def log_prior(self, args):
        logP = 0
        for i in range(self.num_parameters):
            gaussian_prior = self._prior_types[i] == 'gaussian'
            uniform_prior  = self._prior_types[i] == 'uniform'
            logP += lax.cond(gaussian_prior, lambda _: - 0.5 * ((args[i] - self._means[i]) / self._widths[i]) ** 2, lambda _: 0., operand=None)
            logP += lax.cond(uniform_prior, lambda _: lax.cond(args[i] < self._lowers[i], lambda _: - self._unif_prior_penalty, lambda _: 0., operand=None), lambda _: 0., operand=None)
            logP += lax.cond(uniform_prior, lambda _: lax.cond(args[i] > self._uppers[i], lambda _: - self._unif_prior_penalty, lambda _: 0., operand=None), lambda _: 0., operand=None)
        return logP

    def log_prior_gaussian(self, args):
        logP = 0
        for i in range(self.num_parameters):
            gaussian_prior = self._prior_types[i] == 'gaussian'
            logP += lax.cond(gaussian_prior, lambda _: - 0.5 * ((args[i] - self._means[i]) / self._widths[i]) ** 2, lambda _: 0., operand=None)
        return logP

    def log_prior_uniform(self, args):
        logP = 0
        for i in range(self.num_parameters):
            uniform_prior  = self._prior_types[i] == 'uniform'
            logP += lax.cond(uniform_prior, lambda _: lax.cond(args[i] < self._lowers[i], lambda _: - self._unif_prior_penalty, lambda _: 0., operand=None), lambda _: 0., operand=None)
            logP += lax.cond(uniform_prior, lambda _: lax.cond(args[i] > self._uppers[i], lambda _: - self._unif_prior_penalty, lambda _: 0., operand=None), lambda _: 0., operand=None)
        return logP

    def log_prior_nojit(self, args):
        logP = 0
        for i in range(self.num_parameters):
            if self._prior_types[i] == 'gaussian':
                logP += - 0.5 * ((args[i] - self._means[i]) / self._widths[i]) ** 2
            elif self._prior_types[i] == 'uniform' and not (self._lowers[i] <= args[i] <= self._uppers[i]):
                logP += - self._unif_prior_penalty
        return logP

    def _update_arrays(self):
        self._prior_types, self._lowers, self._uppers, self._means, self._widths \
            = self.kwargs2args_prior(self._kwargs_prior)
        self._init_values  = self.kwargs2args(self._kwargs_init)
        self._num_params = len(self._init_values)
        if self.optimized:
            self._map_values = self.kwargs2args(self._kwargs_map)
        if hasattr(self, '_name'):
            delattr(self, '_names')
        if hasattr(self, '_symbols'):
            delattr(self, '_symbols')

    def _get_params(self, args, i, kwargs_model_key, kwargs_key):
        kwargs_list = []
        for k, model in enumerate(self.kwargs_model[kwargs_model_key]):
            kwargs = {}
            kwargs_fixed_k = self._kwargs_fixed[kwargs_key][k]
            param_names = self._get_param_names_for_model(kwargs_key, model)
            for name in param_names:
                if not name in kwargs_fixed_k:
                    if model == 'PIXELATED':
                        if kwargs_key == 'kwargs_lens':
                            n_pix_x, n_pix_y = self._image.LensModel.pixelated_shape
                        elif kwargs_key == 'kwargs_source':
                            n_pix_x, n_pix_y = self._image.SourceModel.pixelated_shape
                        elif kwargs_key == 'kwargs_lens_light':
                            n_pix_x, n_pix_y = self._image.LensLightModel.pixelated_shape
                        num_param = int(n_pix_x * n_pix_y)
                        kwargs['pixels'] = args[i:i + num_param].reshape(n_pix_x, n_pix_y)
                    else:
                        num_param = 1
                        kwargs[name] = args[i]
                    i += num_param
                else:
                    kwargs[name] = kwargs_fixed_k[name]
            kwargs_list.append(kwargs)
        return kwargs_list, i

    def _set_params(self, kwargs, kwargs_model_key, kwargs_key):
        args = []
        for k, model in enumerate(self.kwargs_model[kwargs_model_key]):
            kwargs_profile = kwargs[kwargs_key][k]
            kwargs_fixed_k = self._kwargs_fixed[kwargs_key][k]
            param_names = self._get_param_names_for_model(kwargs_key, model)
            for name in param_names:
                if not name in kwargs_fixed_k:
                    if model == 'PIXELATED':
                        args += kwargs_profile['pixels'].flatten().tolist()
                    else:
                        args.append(kwargs_profile[name])
        return args

    def _set_params_prior(self, kwargs, kwargs_model_key, kwargs_key):
        types, lowers, uppers, means, widths = [], [], [], [], []
        for k, model in enumerate(self.kwargs_model[kwargs_model_key]):
            kwargs_profile = kwargs[kwargs_key][k]
            kwargs_fixed_k = self._kwargs_fixed[kwargs_key][k]
            param_names = self._get_param_names_for_model(kwargs_key, model)
            for name in param_names:
                if not name in kwargs_fixed_k:
                    if name not in kwargs_profile:
                        types.append(None)
                        lowers.append(-np.inf)
                        uppers.append(+np.inf)
                        means.append(np.nan)
                        widths.append(np.nan)

                    else:
                        prior_type = kwargs_profile[name][0]
                        if prior_type == 'uniform':
                            if model == 'PIXELATED':
                                if kwargs_key == 'kwargs_lens':
                                    n_pix_x, n_pix_y = self._image.LensModel.pixelated_shape
                                elif kwargs_key == 'kwargs_source':
                                    n_pix_x, n_pix_y = self._image.SourceModel.pixelated_shape
                                elif kwargs_key == 'kwargs_lens_light':
                                    n_pix_x, n_pix_y = self._image.LensLightModel.pixelated_shape
                                num_param = int(n_pix_x * n_pix_y)
                                types  += [prior_type]*num_param
                                lowers_tmp, uppers_tmp = kwargs_profile['pixels'][1], kwargs_profile['pixels'][2]
                                # those bounds can either be whole array (values per pixel)
                                if isinstance(lowers_tmp, (np.ndarray, jnp.ndarray)):
                                    lowers += lowers_tmp.flatten().tolist()
                                    uppers += uppers_tmp.flatten().tolist()
                                # or they can be single numbers, in which case they are considered the same for pixel
                                elif isinstance(lowers_tmp, (int, float)):
                                    lowers += [float(lowers_tmp)]*num_param
                                    uppers += [float(uppers_tmp)]*num_param
                                means  += [np.nan]*num_param
                                widths += [np.nan]*num_param
                            else:
                                types.append(prior_type)
                                lowers.append(float(kwargs_profile[name][1]))
                                uppers.append(float(kwargs_profile[name][2]))
                                means.append(np.nan)
                                widths.append(np.nan)

                        elif prior_type == 'gaussian':
                            if model == 'PIXELATED':
                                raise ValueError(f"'gaussian' prior for '{model}' model is not supported")
                            else:
                                types.append(prior_type)
                                lowers.append(-np.inf)
                                uppers.append(+np.inf)
                                means.append(kwargs_profile[name][1])
                                widths.append(kwargs_profile[name][2])

                        else:
                            raise ValueError(f"Prior type '{prior_type}' is not supported")
        return types, lowers, uppers, means, widths

    def _set_params_update_fixed(self, kwargs_fixed, kwargs_model_key, kwargs_key):
        for k, model in enumerate(self.kwargs_model[kwargs_model_key]):
            kwargs_fixed_k_old = self._kwargs_fixed[kwargs_key][k]
            kwargs_fixed_k_new = kwargs_fixed
            param_names = self._get_param_names_for_model(kwargs_key, model)
            for name in param_names:
                if name in kwargs_fixed_k_old and name not in kwargs_fixed_k_new:
                    self._kwargs_init[kwargs_key][k][name] = copy.deepcopy(kwargs_fixed_k_old[name])
                    if self.optimized:
                        self._kwargs_map[kwargs_key][k][name] = copy.deepcopy(kwargs_fixed_k_old[name])

    @staticmethod
    def _get_param_names_for_model(kwargs_key, model):
        if kwargs_key in ['kwargs_source', 'kwargs_lens_light']:
            if model == 'GAUSSIAN':
                from jaxtronomy.LightModel.Profiles.gaussian import Gaussian
                profile_class = Gaussian
            elif model == 'SERSIC':
                from jaxtronomy.LightModel.Profiles.sersic import Sersic
                profile_class = Sersic
            elif model == 'SERSIC_ELLIPSE':
                from jaxtronomy.LightModel.Profiles.sersic import SersicElliptic
                profile_class = SersicElliptic
            elif model == 'UNIFORM':
                from jaxtronomy.LightModel.Profiles.uniform import Uniform
                profile_class = Uniform
            elif model == 'PIXELATED':
                from jaxtronomy.LightModel.Profiles.pixelated import Pixelated
                profile_class = Pixelated
        elif kwargs_key == 'kwargs_lens':
            if model == 'SIE':
                from jaxtronomy.LensModel.Profiles.sie import SIE
                profile_class = SIE
            elif model == 'SHEAR':
                from jaxtronomy.LensModel.Profiles.shear import Shear
                profile_class = Shear
            elif model == 'SHEAR_GAMMA_PSI':
                from jaxtronomy.LensModel.Profiles.shear import ShearGammaPsi
                profile_class = ShearGammaPsi
            elif model == 'PIXELATED':
                from jaxtronomy.LensModel.Profiles.pixelated import PixelatedPotential
                profile_class = PixelatedPotential
        return profile_class.param_names

    def _set_names(self, kwargs_model_key, kwargs_key):
        names = []
        for k, model in enumerate(self.kwargs_model[kwargs_model_key]):
            kwargs_fixed_k = self._kwargs_fixed[kwargs_key][k]
            param_names = self._get_param_names_for_model(kwargs_key, model)
            for name in param_names:
                if not name in kwargs_fixed_k:
                    if model == 'PIXELATED':
                        if kwargs_key == 'kwargs_lens':
                            n_pix_x, n_pix_y = self._image.LensModel.pixelated_shape
                            num_param = int(n_pix_x * n_pix_y)
                            names += [f"d_{i}" for i in range(num_param)]  # 'd' for deflector
                        elif kwargs_key == 'kwargs_source':
                            n_pix_x, n_pix_y = self._image.SourceModel.pixelated_shape
                            num_param = int(n_pix_x * n_pix_y)
                            names += [f"s_{i}" for i in range(num_param)]  # 's' for source
                        elif kwargs_key == 'kwargs_lens':
                            n_pix_x, n_pix_y = self._image.LensLightModel.pixelated_shape
                            num_param = int(n_pix_x * n_pix_y)
                            names += [f"dpsi_{i}" for i in range(num_param)]  # 'dpsi' for potential corrections
                    else:
                        names.append(name)
        return names

    def _name2latex(self, names):
        latexs = []
        for name in names:
            # pixelated models
            if name[:2] == 'd_':  
                latex = r"$d_{" + r"{}".format(int(name[2:])) + r"}$"
            elif name[:2] == 's_':  
                latex = r"$s_{" + r"{}".format(int(name[2:])) + r"}$"
            elif name[:5] == 'dpsi_':  
                latex = r"$\delta\psi_{" + r"{}".format(int(name[5:])) + r"}$"
            # other parametric models
            elif name == 'theta_E':
                latex = r"$\theta_{\rm E}$"
            elif name == 'gamma':
                latex = r"$\gamma'$"
            elif name == 'gamma_ext':
                latex = r"$\gamma_{\rm ext}$"
            elif name == 'psi_ext':
                latex = r"$\psi_{\rm ext}$"
            elif name == 'gamma1':
                latex = r"$\gamma_{\rm 1, ext}$"
            elif name == 'gamma2':
                latex = r"$\gamma_{\rm 2, ext}$"
            elif name == 'amp':
                latex = r"$A$"
            elif name == 'R_sersic':
                latex = r"$R_{\rm Sersic}$"
            elif name == 'n_sersic':
                latex = r"$n_{\rm Sersic}$"
            elif name == 'e1':
                latex = r"$e_1$"
            elif name == 'e2':
                latex = r"$e_2$"
            elif name == 'center_x':
                latex = r"$c_{x,0}$"
            elif name == 'center_y':
                latex = r"$c_{y,0}$"
            else:
                raise ValueError("latex symbol for variable '{}' is unknown".format(name))
            latexs.append(latex)
        return latexs

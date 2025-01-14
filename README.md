# Bound propagation
Linear and interval bound propagation in Pytorch with easy-to-use API and GPU support.
Initially made as an alternative to [the original CROWN implementation](https://github.com/IBM/CROWN-Robustness-Certification) which featured only Numpy, lots of for-loops, and a cumbersome API.
This library is comparable to [auto_LiRPA](https://github.com/KaidiXu/auto_LiRPA) but differs in design philosophy - auto_LiRPA parses the ONNX computation graph and operates on generic computation nodes.
The downside of this approach is a giant violation of the Single Resposibility Principle and the Open/Closed Principle of the [SOLID](https://en.wikipedia.org/wiki/SOLID) design principles. 
For this library, we instead assume that the network is structured in a tree of nn.Modules, which we map to BoundModules using a visitor and abstract factory pattern exploiting Python's type system.
This allows better separation of concerns and extensibility (see [New Modules](#new-modules) below), and improved mental map of the library.

To install:
```
pip install bound-propagation
```

Supported bound propagation methods:
- Interval Bound Propagation (IBP)
- [CROWN](https://arxiv.org/abs/1811.00866)
- [CROWN-IBP](https://arxiv.org/abs/1906.06316)

For the examples below assume the following network definition:
```python
from torch import nn
from bound_propagation import BoundModelFactory, HyperRectangle

class Network(nn.Sequential):
    def __init__(self):
        in_size = 30
        classes = 10

        super().__init__(
            nn.Linear(in_size, 16),
            nn.Tanh(),
            nn.Linear(16, 16),
            nn.Tanh(),
            nn.Linear(16, classes)
        )

net = Network()

factory = BoundModelFactory()
net = factory.build(net)
```

The method also works with ```nn.Sigmoid```, ```nn.ReLU```, ```nn.Identity```, and custom non-linear functions: ```Exp```, ```Log```, ```Reciprocal```, ```Sin```, ```Cos```, ```Pow```, ```UnivariateMonomial```, ```MultivariateMonomial```, ```Clamp```.
The following bivariate functions are supported: ```Add```, ```Sub```, ```Div```, and ```Mul```, and their vectorized equivalents.
In addition, we have the following convenience functions: ```Residual```, ```Cat```, ```Parallel```, ```Select```, ```Flip```, ```FixedLinear```, and ```ElementwiseLinear```.
Initial efforts wrt. probability functions such as normal distribution PDF and CDF, and the error function has begun (consult [probability.py](https://github.com/Zinoex/bound_propagation/blob/main/src/bound_propagation/probability.py) for more info).


## Interval bounds
To get interval bounds for either IBP, CROWN, or CROWN-IBP:

```python
x = torch.rand(100, 30)
epsilon = 0.1
input_bounds = HyperRectangle.from_eps(x, epsilon)

ibp_bounds = net.ibp(input_bounds)

crown_bounds = net.crown(input_bounds).concretize()
crown_ibp_bounds = net.crown_ibp(input_bounds).concretize()

alpha_crown_bounds = net.crown(input_bounds, alpha=True).concretize()
alpha_crown_ibp_bounds = net.crown_ibp(input_bounds, alpha=True).concretize()
```

The parameter `alpha=True` enables alpha-CROWN, which means bounds are optimized using projected gradient descent at the cost of more computation.

## Linear bounds
To get linear bounds for either CROWN or CROWN-IBP:

```python
x = torch.rand(100, 30)
epsilon = 0.1
input_bounds = HyperRectangle.from_eps(x, epsilon)

crown_bounds = net.crown(input_bounds)
crown_ibp_bounds = net.crown_ibp(input_bounds)

alpha_crown_bounds = net.crown(input_bounds, alpha=True)
alpha_crown_ibp_bounds = net.crown_ibp(input_bounds, alpha=True)
```
If lower or upper bounds are not needed, then you can add `bound_lower=False` or `bound_lower=True` to avoid the unnecessary computation.
This also works for interval bounds with CROWN and CROWN-IBP.
IBP accepts the two parameters for compatibility but ignores them since both bounds are necessary for the forward propagation. 
Parameter `alpha=True` is explained under [Interval bounds](#interval-bounds).

## New Modules
To show how to design a new module, we use a residual module as a running example (see [residual.py](https://github.com/Zinoex/bound_propagation/blob/main/examples/residual.py) for the full code).
Adding a new module revolves around the assumption that each module as one input and one output, but a module can have submodules such as the case for residual.
```python
class Residual(nn.Module):
    def __init__(self, subnetwork):
        super().__init__()
        self.subnetwork = subnetwork

    def forward(self, x):
        return self.subnetwork(x) + x
```

Next, we build the corresponding BoundModule. The first parameter of the BoundModule is the nn.Module, in this case Residual, and the second is a BoundModelFactory.
Why factory? Because we do not know the structure of the subnetwork, and this could contain new network types defined by users of this library.
So the factory allows us to defer defining how to construct the BoundModule for the subnetwork, and the construction happens by calling `factory.build(subnetwork)` (possibly multiple times if there are multiple subnetworks).
```python
class BoundResidual(BoundModule):
    def __init__(self, model, factory, **kwargs):
        super().__init__(model, factory, **kwargs)
        self.subnetwork = factory.build(model.subnetwork)

    def propagate_size(self, in_size):
        out_size = self.subnetwork.propagate_size(in_size)
        assert in_size == out_size

        return out_size
```
A small note here is the need to implement `propagate_size` - some modules may need to know the input and output size without this being available on the nn.Module.
Hence we propagate the size, and even though we know the output size from the input size for `BoundResidual`, we still propagate through the subnetwork to store this info throughout the bound graph.

Next, we consider linear relaxation for linear bounds - if you only use IBP, you can skip these. BoundModules must implement `need_relaxation` as a property and `clear_relaxation` as a method, and if the module requires relaxation (i.e. not linear and no submodules) it must implement `backward_relaxation` too.
Note here that we let the subnetwork handle the `need_relaxation` since `Residual` itself does not need relaxation but the subnetwork might contain some activation/non-linear function. Similarly, we refer to subnetwork for `clear_relaxation`.
The `backward_relaxation` method is for adding relaxations to the bound graph using CROWN where the top-level function continually calls `backward_relaxation` until no more relaxation is needed as determined by `need_relaxation`.
In this case the method may be called more than once since the subnetwork may have multiple layers needing relaxations, e.g. nn.Sequential with multiple layers of activation functions.
If a module needs relaxation, it starts the backward relaxation chain with identity linear bounds (and zero bias accumulation) and self as the return tuple.
The return signature of `backward_relaxation` is `linear_bounds, relaxation_module` since the top-level method needs a way of referring to the module being relaxed.
```python
    @property
    def need_relaxation(self):
        return self.subnetwork.need_relaxation

    def clear_relaxation(self):
        self.subnetwork.clear_relaxation()

    def backward_relaxation(self, region):
        assert self.subnetwork.need_relaxation
        return self.subnetwork.backward_relaxation(region)
```

Finally, we get to the IBP forward and CROWN backward propagations themselves.
IBP forward is straightforward using interval arithmetic with the exception that you <span style="color:red">must call `bounds.region`</span> as the first parameter on `IntervalBounds` (and `LinearBounds` too).
If not the memory usage will be excessive. 
IBP forward must also propagate `save_relaxation` and if a relaxation is needed for a module the relaxation must be constructed when IBP forward is called - this is to support CROWN-IBP.
Similarly, IBP forward must also propagate `save_input_bounds` and save input if ```True```, which is to improve.
`crown_backward` has two components: updating linear bounds backwards and potentially adding to the bias accumulator.
The structure depends largely on how the nn.Module is, but addition can be done as shown below for `Residual`.
I recommend that you look at [activation.py](https://github.com/Zinoex/bound_propagation/blob/main/src/bound_propagation/activation.py), [linear.py](https://github.com/Zinoex/bound_propagation/blob/main/src/bound_propagation/linear.py), and [cat.py](https://github.com/Zinoex/bound_propagation/blob/main/src/bound_propagation/cat.py) to see how design `crown_backward`.
Note that lower and upper will never interact, and that both can be `None` because we can avoid unnecessary computations if either is not needed.
```python
    def ibp_forward(self, bounds, save_relaxation=False, save_input_bounds=False):
        residual_bounds = self.subnetwork.ibp_forward(bounds, save_relaxation=save_relaxation, save_input_bounds=save_input_bounds)

        return IntervalBounds(bounds.region, bounds.lower + residual_bounds.lower, bounds.upper + residual_bounds.upper)
    
    def crown_backward(self, linear_bounds, optimize):
        residual_linear_bounds = self.subnetwork.crown_backward(linear_bounds, optimize)

        if linear_bounds.lower is None:
            lower = None
        else:
            lower = (linear_bounds.lower[0] + residual_linear_bounds.lower[0], residual_linear_bounds.lower[1])

        if linear_bounds.upper is None:
            upper = None
        else:
            upper = (linear_bounds.upper[0] + residual_linear_bounds.upper[0], residual_linear_bounds.upper[1])

        return LinearBounds(linear_bounds.region, lower, upper)
```

If you use alpha-CROWN and the module contains submodules (or optimizable parameters itself), then the bound module must implement method for collecting bound parameters, projecting gradients, and clipping bound parameters, to support PGD.
```python
    def bound_parameters(self):
        for module in self.bound_sequential:
            yield from module.bound_parameters()

    def clip_params(self):
        for module in self.bound_sequential:
            module.clip_params()

    def project_grads(self):
        for module in self.bound_sequential:
            module.project_grads()
```

The last task is to make a factory and register your new module (alternatively subclass the factory and add to the dictionary of nn.Modules and BoundModules).
```python
factory = BoundModelFactory()
factory.register(Residual, BoundResidual)
```

## Authors
- [Frederik Baymler Mathiesen](https://www.baymler.com) - PhD student @ TU Delft

## Citing
```
@misc{Mathiesen2022,
  author = {Frederik Baymler Mathiesen},
  title = {Bound Propagation},
  year = {2013},
  publisher = {GitHub},
  journal = {GitHub repository},
  howpublished = {\url{https://github.com/Zinoex/bound_propagation}}
}
```

## Funding and support
- TU Delft

## Copyright notice:
Technische Universiteit Delft hereby disclaims all copyright
interest in the program “bound_propagation” 
(bound propagation methods for Pytorch)
written by the Frederik Baymler Mathiesen. Theun Baller, Dean of Mechanical, Maritime and Materials Engineering

© 2022, Frederik Baymler Mathiesen, HERALD Lab, TU Delft

import os
from argparse import ArgumentParser
from math import sqrt

import numpy as np
from tqdm import trange

import torch
from torch import distributions, nn, optim
from torch.utils.data import TensorDataset

from matplotlib import pyplot as plt

from bound_propagation import BoundModelFactory, HyperRectangle, Parallel


def bound_propagation(model, lower_x, upper_x, alpha=False):
    factory = BoundModelFactory()
    bounded_model = factory.build(model)

    input_bounds = HyperRectangle(lower_x, upper_x)

    ibp_bounds = bounded_model.ibp(input_bounds).cpu()
    crown_bounds = bounded_model.crown(input_bounds, alpha=alpha).cpu()

    input_bounds = input_bounds.cpu()

    return input_bounds, ibp_bounds, crown_bounds


def plot_bounds_1d(model, args):
    num_slices = 20

    boundaries = torch.linspace(-2, 2, num_slices + 1, device=args.device).view(-1, 1)
    lower_x, upper_x = boundaries[:-1], boundaries[1:]

    input_bounds, ibp_bounds, crown_bounds = bound_propagation(model, lower_x, upper_x, alpha=True)

    plt.figure(figsize=(6.4 * 2, 3.6 * 2))
    plt.ylim(-2.5, 4)

    for i in range(num_slices):
        x1, x2 = input_bounds.lower[i].item(), input_bounds.upper[i].item()
        y1, y2 = ibp_bounds.lower[i].item(), ibp_bounds.upper[i].item()

        plt.plot([x1, x2], [y1, y1], color='blue', label='IBP lower' if i == 0 else None)
        plt.plot([x1, x2], [y2, y2], color='orange', label='IBP upper' if i == 0 else None)

        y1, y2 = crown_bounds.lower[0][i, 0, 0] * x1 + crown_bounds.lower[1][i], crown_bounds.lower[0][i, 0, 0] * x2 + crown_bounds.lower[1][i]
        y3, y4 = crown_bounds.upper[0][i, 0, 0] * x1 + crown_bounds.upper[1][i], crown_bounds.upper[0][i, 0, 0] * x2 + crown_bounds.upper[1][i]

        y1, y2 = y1.item(), y2.item()
        y3, y4 = y3.item(), y4.item()

        plt.plot([x1, x2], [y1, y2], color='green', label='CROWN lower' if i == 0 else None)
        plt.plot([x1, x2], [y3, y4], color='red', label='CROWN upper' if i == 0 else None)

    X = torch.linspace(-2, 2, 1000, device=args.device).view(-1, 1)
    y = model(X)
    X, y = X.cpu().numpy(), y.cpu().numpy()

    plt.plot(X, y, color='blueviolet', label='Function to bound')

    plt.title(f'Bound propagation')
    plt.legend()

    plt.show()
    # plt.savefig(f'visualization/lbp.pdf', bbox_inches='tight', dpi=300)


def plot_partition(model, args, input_bounds, ibp_bounds, crown_bounds):
    x1, x2 = input_bounds.lower, input_bounds.upper

    plt.clf()
    ax = plt.axes(projection='3d')

    x1, x2 = torch.meshgrid(torch.linspace(x1[0], x2[0], 10), torch.linspace(x1[1], x2[1], 10))

    # Plot IBP
    y1, y2 = ibp_bounds.lower.item(), ibp_bounds.upper.item()
    y1, y2 = torch.full_like(x1, y1), torch.full_like(x1, y2)

    surf = ax.plot_surface(x1, x2, y1, color='blue', label='IBP', alpha=0.4)
    surf._facecolors2d = surf._facecolor3d  # These are hax due to a bug in Matplotlib
    surf._edgecolors2d = surf._edgecolor3d

    surf = ax.plot_surface(x1, x2, y2, color='blue', alpha=0.4)
    surf._facecolors2d = surf._facecolor3d
    surf._edgecolors2d = surf._edgecolor3d

    # Plot LBP
    y_lower = crown_bounds.lower[0][0, 0] * x1 + crown_bounds.lower[0][0, 1] * x2 + crown_bounds.lower[1]
    y_upper = crown_bounds.upper[0][0, 0] * x1 + crown_bounds.upper[0][0, 1] * x2 + crown_bounds.upper[1]

    surf = ax.plot_surface(x1, x2, y_lower, color='green', label='CROWN', alpha=0.4, shade=False)
    surf._facecolors2d = surf._facecolor3d
    surf._edgecolors2d = surf._edgecolor3d

    surf = ax.plot_surface(x1, x2, y_upper, color='green', alpha=0.4, shade=False)
    surf._facecolors2d = surf._facecolor3d
    surf._edgecolors2d = surf._edgecolor3d

    # Plot function
    x1, x2 = input_bounds.lower, input_bounds.upper
    x1, x2 = torch.meshgrid(torch.linspace(x1[0], x2[0], 50), torch.linspace(x1[1], x2[1], 50))
    X = torch.cat(tuple(torch.dstack([x1, x2]))).to(args.device)
    y = model(X).view(50, 50)
    y = y.cpu()

    surf = ax.plot_surface(x1, x2, y, color='red', label='Function to bound', shade=False)
    surf._facecolors2d = surf._facecolor3d
    surf._edgecolors2d = surf._edgecolor3d

    # General plot config
    plt.xlabel('x')
    plt.ylabel('y')

    plt.title(f'Bound propagation')
    plt.legend()

    plt.show()


def plot_bounds_2d(model, args):
    num_slices = 20

    x_space = torch.linspace(-2.0, 2.0, num_slices + 1, device=args.device)
    cell_width = (x_space[1] - x_space[0]) / 2
    slice_centers = (x_space[:-1] + x_space[1:]) / 2

    cell_centers = torch.cartesian_prod(slice_centers, slice_centers)
    lower_x, upper_x = cell_centers - cell_width, + cell_centers + cell_width

    input_bounds, ibp_bounds, crown_bounds = bound_propagation(model, lower_x, upper_x, alpha=True)

    # Plot function over entire space
    plt.clf()
    ax = plt.axes(projection='3d')

    x1, x2 = torch.meshgrid(torch.linspace(-2.0, 2.0, 500), torch.linspace(-2.0, 2.0, 500))
    X = torch.cat(tuple(torch.dstack([x1, x2]))).to(args.device)
    y = model(X).view(500, 500)
    y = y.cpu()

    surf = ax.plot_surface(x1, x2, y, color='red', alpha=0.8)
    surf._facecolors2d = surf._facecolor3d
    surf._edgecolors2d = surf._edgecolor3d

    # General plot config
    plt.xlabel('x')
    plt.ylabel('y')

    plt.title(f'Sine approximator & partitioning')
    plt.show()

    for i in trange(num_slices ** 2):
        plot_partition(model, args, input_bounds[i], ibp_bounds[i], crown_bounds[i])


@torch.no_grad()
def plot_bounds(model, args):
    if args.dim == 1:
        plot_bounds_1d(model, args)
    elif args.dim == 2:
        plot_bounds_2d(model, args)
    else:
        raise NotImplementedError()


def evaluate(model, args):
    criterion = nn.MSELoss()

    dataset = NoisySineDataset(dim=args.dim)
    X_train, y_train = dataset[:]
    X, y = X_train.to(args.device), y_train.to(args.device)

    y_pred = model(X)
    loss = criterion(y_pred, y)

    print(f'MSE: {loss.item()}')


@torch.no_grad()
def test(model, args):
    os.makedirs('visualization', exist_ok=True)

    evaluate(model, args)
    plot_bounds(model, args)


def noise(train_size, sigma):
    dist = distributions.Normal(0.0, sigma)

    train_size = (train_size,)
    return dist.sample(train_size)


def f_1d(train_size, sigma):
    X = torch.linspace(-1.0, 1.0, train_size).view(-1, 1)
    return X, torch.sin(2 * np.pi * X) + noise(train_size, sigma).view(-1, 1)


def f_2d(train_size, sigma):
    x_space = torch.linspace(-1.0, 1.0, int(sqrt(train_size)))
    X = torch.cartesian_prod(x_space, x_space)
    y = 0.5 * torch.sin(2 * np.pi * X[:, 0]) + 0.5 * torch.sin(2 * np.pi * X[:, 1]) + noise(train_size, sigma)

    return X, y.view(-1, 1)


class NoisySineDataset(TensorDataset):
    def __init__(self, dim=1, sigma=0.05, train_size=2 ** 10):
        if dim == 1:
            X, y = f_1d(train_size, sigma)
        elif dim == 2:
            X, y = f_2d(train_size, sigma)
        else:
            raise NotImplementedError()

        super().__init__(X, y)


class Model(nn.Sequential):
    def __init__(self, dim=1):
        super().__init__(
            nn.Linear(dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            Parallel(nn.ReLU(), nn.Tanh(), split_size=32),
            nn.Linear(64, 1)
        )


def train(model, args, eps=0.005):
    dataset = NoisySineDataset(dim=args.dim, train_size=2**12)
    X, y = dataset[:]
    X, y = X.to(args.device), y.to(args.device)

    factory = BoundModelFactory()
    bounded_model = factory.build(model)

    optimizer = optim.Adam(model.parameters(), lr=4e-3)
    criterion = nn.MSELoss(reduction='none')

    for epoch in trange(1000):
        optimizer.zero_grad(set_to_none=True)

        y_pred = model(X)
        loss = criterion(y_pred, y).mean()

        interval_bounds = bounded_model.ibp(HyperRectangle.from_eps(X, eps))
        loss = loss + torch.max(criterion(interval_bounds.lower, y), criterion(interval_bounds.upper, y)).mean()

        loss.backward()
        optimizer.step()

        if epoch % 100 == 99:
            with torch.no_grad():
                y_pred = model(X)
                loss = criterion(y_pred, y).mean().item()

                print(f"loss: {loss:>7f}")


def main(args):
    net = Model(dim=args.dim).to(args.device)

    train(net, args)
    test(net, args)


def parse_arguments():
    parser = ArgumentParser()
    parser.add_argument('--device', choices=list(map(torch.device, ['cuda', 'cpu'])), type=torch.device, default='cuda',
                        help='Select device for tensor operations')
    parser.add_argument('--dim', choices=[1, 2], type=int, default=1, help='Dimensionality of the noisy sine')

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()
    main(args)

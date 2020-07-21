# Cycle-GAN 3D main

import argparse
import itertools
import os, sys
from torchvision.utils import save_image
from torch.utils.data import DataLoader
from torch.autograd import Variable
import torch
import torch.nn.functional as F
import requests

from GAN import Generator, Discriminator, ReplayBuffer, LambdaLR, Logger, weights_init_normal, tensor2image
from GAN_prep import ImageDataset, construct, sampling

# Train
parser = argparse.ArgumentParser()
parser.add_argument('--mode', type=str, default='train', help='train or test')
parser.add_argument('--epoch', type=int, default=0, help='starting epoch')
parser.add_argument('--n_epochs', type=int, default=2, help='number of epochs of training')
parser.add_argument('--batchSize', type=int, default=1, help='size of the batches')
parser.add_argument('--dataroot', type=str, default='../Results/trial', help='root directory of the dataset')
parser.add_argument('--n_data', type=int, default=200, help='number of images of training (round to nearest 200)')
parser.add_argument('--lr', type=float, default=0.0002, help='initial learning rate')
parser.add_argument('--decay_epoch', type=int, default=0,
                    help='epoch to start linearly decaying the learning rate to 0')
parser.add_argument('--size', type=int, default=256, help='size of the data crop (squared assumed)')
parser.add_argument('--stack', type=int, default=7, help='depth of data crop')
parser.add_argument('--input_nc', type=int, default=1, help='number of channels of input data')
parser.add_argument('--output_nc', type=int, default=1, help='number of channels of output data')
parser.add_argument('--cuda', action='store_true', help='use GPU computation')
parser.add_argument('--n_cpu', type=int, default=8, help='number of cpu threads to use during batch generation')
parser.add_argument('--generator_A2B', type=str, default='../Results/trial/netG_A2B.pth',
                    help='Fluorescence to Binary generator checkpoint file')
parser.add_argument('--generator_B2A', type=str, default='../Results/trial/netG_B2A.pth',
                    help='Binary to Fluorescence generator checkpoint file')
opt = parser.parse_args()
if opt.decay_epoch == 0:
    opt.decay_epoch = int(opt.n_epochs/2)
print(opt, flush=True)

if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda", flush=True)

if not os.path.exists(opt.dataroot):
    os.makedirs(opt.dataroot)
if not os.path.exists(opt.dataroot + '/data'):
    os.makedirs(opt.dataroot + '/data')
if not os.path.exists(opt.dataroot + '/out'):
    os.makedirs(opt.dataroot + '/out')

###### Definition of variables ######
# Networks
netG_A2B = Generator(opt.input_nc, opt.output_nc)
netG_B2A = Generator(opt.output_nc, opt.input_nc)
if opt.mode == 'train':
    netD_A = Discriminator(opt.input_nc)
    netD_B = Discriminator(opt.output_nc)

if opt.cuda:
    netG_A2B.cuda()
    netG_B2A.cuda()
    if opt.mode == 'train':
        netD_A.cuda()
        netD_B.cuda()

if opt.mode == 'train':
    # Data augmentation and prep
    for numm in range(int(opt.n_data/200)):
        bigimage, biglabel = construct('../train3D')
        sampling(bigimage, biglabel, numm+1, opt.dataroot + '/data', rand_num=56)

    netG_A2B.apply(weights_init_normal)
    netG_B2A.apply(weights_init_normal)
    netD_A.apply(weights_init_normal)
    netD_B.apply(weights_init_normal)

    # Lossess
    criterion_GAN = torch.nn.MSELoss()
    criterion_cycle = torch.nn.L1Loss()
    criterion_identity = torch.nn.L1Loss()

    # Optimizers & LR schedulers
    optimizer_G = torch.optim.Adam(itertools.chain(netG_A2B.parameters(), netG_B2A.parameters()),
                                   lr=opt.lr, betas=(0.5, 0.999))
    optimizer_D_A = torch.optim.Adam(netD_A.parameters(), lr=opt.lr, betas=(0.5, 0.999))
    optimizer_D_B = torch.optim.Adam(netD_B.parameters(), lr=opt.lr, betas=(0.5, 0.999))

    lr_scheduler_G = torch.optim.lr_scheduler.LambdaLR(optimizer_G,
                                                       lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step)
    lr_scheduler_D_A = torch.optim.lr_scheduler.LambdaLR(optimizer_D_A,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step)
    lr_scheduler_D_B = torch.optim.lr_scheduler.LambdaLR(optimizer_D_B,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step)

    # Inputs & targets memory allocation
    Tensor = torch.cuda.FloatTensor if opt.cuda else torch.Tensor
    input_A = Tensor(opt.batchSize, opt.input_nc, opt.stack, opt.size, opt.size)
    input_B = Tensor(opt.batchSize, opt.output_nc, opt.stack, opt.size, opt.size)
    target_real = Variable(Tensor(opt.batchSize, opt.output_nc).fill_(1.0), requires_grad=False)
    target_fake = Variable(Tensor(opt.batchSize, opt.output_nc).fill_(0.0), requires_grad=False)

    fake_A_buffer = ReplayBuffer()
    fake_B_buffer = ReplayBuffer()

    # Dataset loader

    dataloader = DataLoader(ImageDataset(opt.dataroot, unaligned=True),
                            batch_size=opt.batchSize, shuffle=True, num_workers=opt.n_cpu)

    # Loss plot
    try:
        logger = Logger(opt.n_epochs, len(dataloader), opt.dataroot + '/out/log.txt')
    except requests.exceptions.ConnectionError:
        pass

    ###### Training ######
    for epoch in range(opt.epoch, opt.n_epochs):
        print('starting epoch: ', epoch+1, flush=True)
        for i, batch in enumerate(dataloader):
            # Set model input
            real_A = Variable(input_A.copy_(batch['Fl']))
            real_B = Variable(input_B.copy_(batch['Bn']))

            ###### Generators A2B and B2A ######
            optimizer_G.zero_grad()

            # Identity loss
            # G_A2B(B) should equal B if real B is fed
            same_B = netG_A2B(real_B)
            same_B = F.interpolate(same_B, [opt.stack, opt.size, opt.size])
            loss_identity_B = criterion_identity(same_B, real_B)
            # G_B2A(A) should equal A if real A is fed
            same_A = netG_B2A(real_A)
            same_A = F.interpolate(same_A, [opt.stack, opt.size, opt.size])
            loss_identity_A = criterion_identity(same_A, real_A)

            # GAN loss
            fake_B = netG_A2B(real_A)
            fake_B = F.interpolate(fake_B, [opt.stack, opt.size, opt.size])
            pred_fake = netD_B(fake_B)
            loss_GAN_A2B = criterion_GAN(pred_fake, target_real)

            fake_A = netG_B2A(real_B)
            fake_A = F.interpolate(fake_A, [opt.stack, opt.size, opt.size])
            pred_fake = netD_A(fake_A)
            loss_GAN_B2A = criterion_GAN(pred_fake, target_real)

            # Cycle loss
            recovered_A = netG_B2A(fake_B)
            recovered_A = F.interpolate(recovered_A, [opt.stack, opt.size, opt.size])
            loss_cycle_ABA = criterion_cycle(recovered_A, real_A)

            recovered_B = netG_A2B(fake_A)
            recovered_B = F.interpolate(recovered_B, [opt.stack, opt.size, opt.size])
            loss_cycle_BAB = criterion_cycle(recovered_B, real_B)

            # Total loss
            loss_G = loss_identity_A + loss_identity_B + loss_GAN_A2B + loss_GAN_B2A + loss_cycle_ABA + loss_cycle_BAB
            loss_G.backward()

            optimizer_G.step()
            ###################################

            ###### Discriminator A ######
            optimizer_D_A.zero_grad()

            # Real loss
            pred_real = netD_A(real_A)
            loss_D_real = criterion_GAN(pred_real, target_real)

            # Fake loss
            fake_A = fake_A_buffer.push_and_pop(fake_A)
            pred_fake = netD_A(fake_A.detach())
            loss_D_fake = criterion_GAN(pred_fake, target_fake)

            # Total loss
            loss_D_A = (loss_D_real + loss_D_fake) * 0.5
            loss_D_A.backward()

            optimizer_D_A.step()
            ###################################

            ###### Discriminator B ######
            optimizer_D_B.zero_grad()

            # Real loss
            pred_real = netD_B(real_B)
            loss_D_real = criterion_GAN(pred_real, target_real)

            # Fake loss
            fake_B = fake_B_buffer.push_and_pop(fake_B)
            pred_fake = netD_B(fake_B.detach())
            loss_D_fake = criterion_GAN(pred_fake, target_fake)

            # Total loss
            loss_D_B = (loss_D_real + loss_D_fake) * 0.5
            loss_D_B.backward()

            optimizer_D_B.step()
            ###################################
            # Progress report (http://localhost:8097)
            logger.log({'loss_G': loss_G, 'loss_G_identity': (loss_identity_A + loss_identity_B),
                        'loss_G_GAN': (loss_GAN_A2B + loss_GAN_B2A),
                        'loss_G_cycle': (loss_cycle_ABA + loss_cycle_BAB), 'loss_D': (loss_D_A + loss_D_B)},
                       images={'real_A': real_A, 'real_B': real_B, 'fake_A': fake_A, 'fake_B': fake_B})
            print("\n", flush=True)
        # Update learning rates
        lr_scheduler_G.step()
        lr_scheduler_D_A.step()
        lr_scheduler_D_B.step()

        # Save models checkpoints
        torch.save(netG_A2B.state_dict(), opt.dataroot+'/netG_A2B.pth')
        torch.save(netG_B2A.state_dict(), opt.dataroot+'/netG_B2A.pth')
        torch.save(netD_A.state_dict(), opt.dataroot+'/netD_A.pth')
        torch.save(netD_B.state_dict(), opt.dataroot+'/netD_B.pth')

elif opt.mode == 'test':
    # Load state dicts
    netG_A2B.load_state_dict(torch.load(opt.generator_A2B))
    netG_B2A.load_state_dict(torch.load(opt.generator_B2A))

    # Set model's test mode
    netG_A2B.eval()
    netG_B2A.eval()

    # Inputs & targets memory allocation
    Tensor = torch.cuda.FloatTensor if opt.cuda else torch.Tensor
    input_A = Tensor(opt.batchSize, opt.input_nc, opt.stack, opt.size, opt.size)
    input_B = Tensor(opt.batchSize, opt.output_nc, opt.stack, opt.size, opt.size)

    # Dataset loader
    dataloader = DataLoader(ImageDataset(opt.dataroot),
                            batch_size=opt.batchSize, shuffle=False, num_workers=opt.n_cpu)

    ###### Testing######
    for i, batch in enumerate(dataloader):
        # Set model input
        real_A = Variable(input_A.copy_(batch['Fl']))
        real_B = Variable(input_B.copy_(batch['Bn']))

        # Generate output
        fake_B = 0.5 * (netG_A2B(real_A).data + 1.0)
        fake_A = 0.5 * (netG_B2A(real_B).data + 1.0)

        # Save image files
        save_image(fake_A, opt.dataroot+'/out/Fl_%04d.png' % (i + 1))
        save_image(fake_B, opt.dataroot+'/out/Bn_%04d.png' % (i + 1))

        sys.stdout.write('\rGenerated images %04d of %04d' % (i + 1, len(dataloader)))

    sys.stdout.write('\n')

else:
    print("Please check your input arguments!", flush=True)
    sys.exit(0)
###################################

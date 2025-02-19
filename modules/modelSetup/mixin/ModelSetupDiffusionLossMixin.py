from abc import ABCMeta

import torch
import torch.nn.functional as F
from torch import Tensor

from modules.module.AestheticScoreModel import AestheticScoreModel
from modules.module.HPSv2ScoreModel import HPSv2ScoreModel
from modules.util import loss_util
from modules.util.args.TrainArgs import TrainArgs
from modules.util.enum.AlignPropLoss import AlignPropLoss


class ModelSetupDiffusionLossMixin(metaclass=ABCMeta):
    def __init__(self):
        super(ModelSetupDiffusionLossMixin, self).__init__()
        self.align_prop_loss_fn = None

    def _get_lora_rank(
            self,
            state_dict: dict,
    ) -> int:
        for name, state in state_dict.items():
            if name.endswith("lora_down.weight"):
                return state.shape[0]

    def _diffusion_loss(
            self,
            batch: dict,
            data: dict,
            args: TrainArgs,
            train_device: torch.device,
    ) -> Tensor:
        if data['loss_type'] == 'align_prop':
            if self.align_prop_loss_fn is None:
                dtype = data['predicted'].dtype

                match args.align_prop_loss:
                    case AlignPropLoss.HPS:
                        self.align_prop_loss_fn = HPSv2ScoreModel(dtype)
                    case AlignPropLoss.AESTHETIC:
                        self.align_prop_loss_fn = AestheticScoreModel()

                self.align_prop_loss_fn.to(device=train_device, dtype=dtype)
                self.align_prop_loss_fn.requires_grad_(False)
                self.align_prop_loss_fn.eval()

            match args.align_prop_loss:
                case AlignPropLoss.HPS:
                    with torch.autocast(device_type=train_device.type, dtype=data['predicted'].dtype):
                        losses = self.align_prop_loss_fn(data['predicted'], batch['prompt'], train_device)
                case AlignPropLoss.AESTHETIC:
                    losses = self.align_prop_loss_fn(data['predicted'])

            losses = losses * args.align_prop_weight
        else:
            # TODO: don't disable masked loss functions when has_conditioning_image_input is true.
            #  This breaks if only the VAE is trained, but was loaded from an inpainting checkpoint
            if args.masked_training and not args.model_type.has_conditioning_image_input():
                losses = loss_util.masked_loss(
                    F.mse_loss,
                    data['predicted'],
                    data['target'],
                    batch['latent_mask'],
                    args.unmasked_weight,
                    args.normalize_masked_area_loss
                ).mean([1, 2, 3])
            else:
                losses = F.mse_loss(
                    data['predicted'],
                    data['target'],
                    reduction='none'
                ).mean([1, 2, 3])

                if args.normalize_masked_area_loss:
                    clamped_mask = torch.clamp(batch['latent_mask'], args.unmasked_weight, 1)
                    losses = losses / clamped_mask.mean(dim=(1, 2, 3))

        return losses.mean()

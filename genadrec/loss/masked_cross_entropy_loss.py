import torch
import torch.nn.functional as F
from torch import nn
from typing import List, Tuple, Union, Optional


def cross_entropy(logits, targets):
    mask = (targets != -1)
    return F.cross_entropy(logits[mask], targets[mask])


class MaskedCrossEntropyLoss(nn.Module):
    def forward(
        self, 
        logits: List[torch.Tensor], 
        logit_masks: List[Optional[torch.Tensor]],
        targets: torch.Tensor,
        penalize_masked: bool = True,
    ):
        assert len(logits) == len(logit_masks), "logits and logit_masks must have same length"
        assert len(logits) == targets.shape[1], "as many logits as target columns must be provided"

        loss = 0
        for i, (logit, logit_mask) in enumerate(zip(logits, logit_masks)):
            if logit_mask is None:
                loss += cross_entropy(logit, targets[:, i])
            else:
                assert logit.shape == logit_mask.shape, "logit and logit_mask must have same shape"

                if penalize_masked:
                    mask_loss = torch.sum(logit.exp() * logit_mask, dim=1)
                    loss += mask_loss.mean()

                categorical_loss = cross_entropy(logit.masked_fill(logit_mask, float('-inf')), targets[:, i])
                loss += categorical_loss

        return loss

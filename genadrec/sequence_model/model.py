import torch
import torch.nn as nn
from dataset.interactions import CategoricalFeature
from embedding.user import UserIdTower
from embedding.ads import AdTower
from itertools import chain
from loss.softmax import SampledSoftmaxLoss
from model.seq import RNN
from simple_ML_baseline.taobao_behavior_dataset import AdBatch
from simple_ML_baseline.taobao_behavior_dataset import TaobaoInteractionsSeqBatch
from typing import List


class RNNSeqModel(nn.Module):
    def __init__(self,
                 n_users: int,
                 ad_categorical_feats: List[CategoricalFeature],
                 cell_type: str,
                 rnn_input_size,
                 rnn_hidden_size,
                 device,
                 embedder_hidden_dims,
                 rnn_batch_first=True,
                 ) -> None:
        
        super().__init__()
        
        self.rnn = RNN(
            cell_type=cell_type,
            input_size=rnn_input_size,
            hidden_size=rnn_hidden_size,
            batch_first=rnn_batch_first
        )

        # self.user_embedding = UserIdTower(
        #     n_users=n_users,
        #     embedding_dim=rnn_hidden_size,
        #     hidden_dims=embedder_hidden_dims,
        #     device=device
        # )

        self.ad_embedding = AdTower(
            categorical_features=ad_categorical_feats,
            embedding_dim=rnn_input_size,
            hidden_dims=embedder_hidden_dims,
            device=device
        )

        self.action_embedding = nn.Embedding(3, embedding_dim=rnn_input_size, padding_idx=1, max_norm=1, device=device)
        self.sampled_softmax = SampledSoftmaxLoss()
        self.device = device
    
    def dense_grad_parameters(self):
        return chain(
            self.action_embedding.parameters(),
            self.ad_embedding.mlp.parameters(),
            self.rnn.parameters()
        )

    def sparse_grad_parameters(self):
        return self.ad_embedding.ad_embedder.parameters()

    def forward(self, batch: TaobaoInteractionsSeqBatch):
        # user_emb = self.user_embedding(batch.user_feats)
        ad_emb = self.ad_embedding(batch.ad_feats)
        action = batch.is_click + 1
        action_emb = self.action_embedding(action)
        is_click = batch.is_click == 1

        B, L, D = ad_emb.shape

        input_emb = ad_emb + action_emb
        mask = (
            (~batch.is_padding).unsqueeze(1) &
            (batch.timestamp.unsqueeze(2) >= batch.timestamp.unsqueeze(1)) &
            (batch.ad_feats.adgroup_id.unsqueeze(2) != batch.ad_feats.adgroup_id.unsqueeze(1)) & 
            (batch.is_click == -1).unsqueeze(1)
        )[:, 1:, :]
        pos_neg_mask_expanded = torch.zeros_like(mask).unsqueeze(3).repeat(1, 1, 1, B)
        indices = torch.arange(B, dtype=torch.int32, device=self.device)
        pos_neg_mask_expanded[indices, :, :, indices] = mask

        shifted_is_click = is_click[:, 1:]
        model_output = self.rnn(input_emb)[:, :-1, :]
        
        pos_neg_mask = torch.flatten(pos_neg_mask_expanded[shifted_is_click], start_dim=1, end_dim=2)
        target_emb = ad_emb[:, 1:, :][shifted_is_click, :]
        pos_emb = model_output[shifted_is_click, :]
        q_probas = batch.ad_feats.rel_ad_freqs.to(torch.float32)
        neg_emb = ad_emb.reshape(-1, D)
        pos_q_probas = q_probas[:, 1:][shifted_is_click]

        loss = self.sampled_softmax.forward(
            pos_emb=pos_emb,
            target_emb=target_emb,
            neg_emb=neg_emb,
            pos_q_probas=pos_q_probas,
            neg_q_probas=q_probas.flatten(),
            pos_neg_mask=pos_neg_mask.to(torch.float32)
        )

        return loss
    
    def eval_forward(self, batch: TaobaoInteractionsSeqBatch):
        ad_emb = self.ad_embedding(batch.ad_feats)
        action = batch.is_click + 1
        action_emb = self.action_embedding(action)
        
        input_emb = ad_emb + action_emb
        output_emb = self.rnn(input_emb)

        B, L, D = ad_emb.shape
        
        target_idx = (~batch.is_padding).sum(axis=1).unsqueeze(1) - 1
        batch_idx = torch.arange(B, device=ad_emb.device)
        target_emb = torch.diagonal(ad_emb[batch_idx, target_idx, :], dim1=0, dim2=1).T
        pred_emb = torch.diagonal(output_emb[batch_idx, target_idx-1, :], dim1=0, dim2=1).T

        return pred_emb, target_emb
    
    def ad_forward(self, batch: AdBatch):
        return self.ad_embedding(batch).squeeze(0)
        

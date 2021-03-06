import os
import json
import argparse
from datetime import datetime
from tools.model import TagtreeFineTuningModel
import options
from pprint import pprint
from data_builder.data_util import get_op_1, get_arithmetic_op_index_1, get_op_2, get_arithmetic_op_index_2
from data_builder.data_util import get_op_3, get_arithmetic_op_index_3
from data_builder.data_util import OPERATOR_CLASSES_
from tools.utils import create_logger, set_environment
from data_builder.tatqa_roberta_tagtree_batch_gen import TaTQABatchGen, TaTQATestBatchGen
from transformers import RobertaModel, BertModel
from tagop.newmo import MutiHeadModel
import torch
import numpy as np

parser = argparse.ArgumentParser("Tagop training task.")
options.add_data_args(parser)
options.add_train_args(parser)
options.add_bert_args(parser)
parser.add_argument("--encoder", type=str, default='roberta')
parser.add_argument("--op_mode", type=int, default=0)
parser.add_argument("--finbert_model", type=str, default='dataset_tagop/finbert')
parser.add_argument("--ablation_mode", type=int, default=0)
parser.add_argument("--test_data_dir", type=str, default="tagtree/data/roberta")

args = parser.parse_args()
if args.ablation_mode != 0:
    args.save_dir = args.save_dir + "_{}_{}".format(args.op_mode, args.ablation_mode)
    args.data_dir = args.data_dir + "_{}_{}".format(args.op_mode, args.ablation_mode)

if not os.path.exists(args.save_dir):
    os.mkdir(args.save_dir)

args.cuda = args.gpu_num > 0
args_path = os.path.join(args.save_dir, "args.json")
with open(args_path, "w") as f:
    json.dump((vars(args)), f)

args.batch_size = args.batch_size // args.gradient_accumulation_steps

logger = create_logger("Roberta Training", log_file=os.path.join(args.save_dir, args.log_file))

pprint(args)
set_environment(args.seed, args.cuda)


def main():
    # best_result = float("-inf")
    logger.info("Loading data...")

    train_itr = TaTQABatchGen(args, data_mode="train", encoder=args.encoder)
    dev_itr = TaTQABatchGen(args, data_mode="dev", encoder=args.encoder)

    num_train_steps = int(args.max_epoch * len(train_itr) / args.gradient_accumulation_steps)
    logger.info("Num update steps {}!".format(num_train_steps))

    logger.info(f"Build {args.encoder} model.")
    if args.encoder == 'bert':
        bert_model = BertModel.from_pretrained('bert-large-uncased')
    elif args.encoder == 'roberta':
        bert_model = RobertaModel.from_pretrained(args.roberta_model)
    elif args.encoder == 'finbert':
        bert_model = BertModel.from_pretrained(args.finbert_model)

    if args.ablation_mode == 0:
        operators = OPERATOR_CLASSES_
    elif args.ablation_mode == 1:
        operators = get_op_1(args.op_mode)
    elif args.ablation_mode == 2:
        operators = get_op_2(args.op_mode)
    else:
        operators = get_op_3(args.op_mode)
    if args.ablation_mode == 0:
        arithmetic_op_index = [3, 4, 6, 7, 8, 9]
    elif args.ablation_mode == 1:
        arithmetic_op_index = get_arithmetic_op_index_1(args.op_mode)
    elif args.ablation_mode == 2:
        arithmetic_op_index = get_arithmetic_op_index_2(args.op_mode)
    else:
        arithmetic_op_index = get_arithmetic_op_index_3(args.op_mode)

    network = MutiHeadModel(bert=bert_model,
                            config=bert_model.config,
                            operator_classes=len(operators),
                            scale_classes=5,
                            arithmetic_op_index=arithmetic_op_index,
                            op_mode=args.op_mode,
                            ablation_mode=args.ablation_mode, )

    model = TagtreeFineTuningModel(args, network, num_train_steps=num_train_steps)
    train_start = datetime.now()
    first = True
    for epoch in range(1, args.max_epoch + 1):
        model.reset()
        if not first:
            train_itr.reset()
        first = False
        logger.info('At epoch {}'.format(epoch))
        for step, batch in enumerate(train_itr):
            model.update(batch)
            if model.step % (args.log_per_updates * args.gradient_accumulation_steps) == 0 or model.step == 1:
                logger.info("Updates[{0:6}] train loss[{1:.5f}] head acc[{2:.5f}]remaining[{3}].\r\n".format(
                    model.updates, model.train_loss.avg, model.head_acc.avg,
                    str((datetime.now() - train_start) / (step + 1) * (num_train_steps - step - 1)).split('.')[0]))
                model.avg_reset()

        model.reset()
        model.avg_reset()
        model.evaluate(dev_itr)
        logger.info("Evaluate epoch:[{0:6}] eval loss[{1:.5f}] head acc[{2:.5f}].\r\n".format(epoch, model.dev_loss.avg,
                                                                                              model.head_acc.avg))
        model.avg_reset()


if __name__ == "__main__":
    main()

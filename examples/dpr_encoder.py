# fmt: off
import logging
import os
import pprint
from pathlib import Path

from farm.data_handler.data_silo import DataSilo
from farm.data_handler.processor import TextSimilarityProcessor
from farm.modeling.biadaptive_model import BiAdaptiveModel
from farm.modeling.language_model import LanguageModel
from farm.modeling.optimization import initialize_optimizer
from farm.modeling.prediction_head import TextSimilarityHead
from farm.modeling.tokenization import Tokenizer
from farm.train import Trainer
from farm.utils import set_all_seeds, MLFlowLogger, initialize_device_settings
from farm.eval import Evaluator

def dense_passage_retrieval():
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO,
    )

    ml_logger = MLFlowLogger(tracking_uri="https://public-mlflow.deepset.ai/")
    ml_logger.init_experiment(experiment_name="FARM-dense_passage_retrieval", run_name="Run_dpr_enocder")

    ##########################
    ########## Settings
    ##########################
    set_all_seeds(seed=42)
    device, n_gpu = initialize_device_settings(use_cuda=True)
    batch_size = 2
    n_epochs = 3
    evaluate_every = 1000
    question_lang_model = "facebook/dpr-question_encoder-single-nq-base"
    passage_lang_model = "facebook/dpr-ctx_encoder-single-nq-base"
    do_lower_case = True
    use_fast = True
    embed_title = True
    num_hard_negatives = 1
    similarity_function = "dot_product"
    train_filename = "nq-train.json"
    dev_filename = "nq-dev.json"
    max_samples = None #load a smaller dataset (e.g. for debugging)

    # 1.Create question and passage tokenizers
    query_tokenizer = Tokenizer.load(pretrained_model_name_or_path=question_lang_model,
                                     do_lower_case=do_lower_case, use_fast=use_fast)
    context_tokenizer = Tokenizer.load(pretrained_model_name_or_path=passage_lang_model,
                                       do_lower_case=do_lower_case, use_fast=use_fast)

    # 2. Create a DataProcessor that handles all the conversion from raw text into a pytorch Dataset
    # data_dir "data/retriever" should contain DPR training and dev files downloaded from https://github.com/facebookresearch/DPR
    # i.e., nq-train.json, nq-dev.json or trivia-train.json, trivia-dev.json
    label_list = ["hard_negative", "positive"]
    metric = "text_similarity_metric"
    processor = TextSimilarityProcessor(tokenizer=query_tokenizer,
                             passage_tokenizer=context_tokenizer,
                             max_seq_len_query=256,
                             max_seq_len_context=256,
                             label_list=label_list,
                             metric=metric,
                             data_dir="data/retriever",
                             train_filename=train_filename,
                             dev_filename=dev_filename,
                             test_filename=dev_filename,
                             embed_title=embed_title,
                             num_hard_negatives=num_hard_negatives,
                             max_samples=max_samples)

    # 3. Create a DataSilo that loads several datasets (train/dev/test), provides DataLoaders for them and calculates a few descriptive statistics of our datasets
    # NOTE: In FARM, the dev set metrics differ from test set metrics in that they are calculated on a token level instead of a word level
    data_silo = DataSilo(processor=processor, batch_size=batch_size, distributed=False)


    # 4. Create an AdaptiveModel+
    # a) which consists of a pretrained language model as a basis
    question_language_model = LanguageModel.load(pretrained_model_name_or_path="bert-base-uncased", language_model_class="DPRQuestionEncoder")
    passage_language_model = LanguageModel.load(pretrained_model_name_or_path="bert-base-uncased", language_model_class="DPRContextEncoder")


    # b) and a prediction head on top that is suited for our task => Question Answering
    prediction_head = TextSimilarityHead(similarity_function=similarity_function)

    model = BiAdaptiveModel(
        language_model1=question_language_model,
        language_model2=passage_language_model,
        prediction_heads=[prediction_head],
        embeds_dropout_prob=0.1,
        lm1_output_types=["per_sequence"],
        lm2_output_types=["per_sequence"],
        device=device,
    )

    # 5. Create an optimizer
    model, optimizer, lr_schedule = initialize_optimizer(
        model=model,
        learning_rate=1e-5,
        optimizer_opts={"name": "TransformersAdamW", "correct_bias": True, "weight_decay": 0.0, \
                        "eps": 1e-08},
        schedule_opts={"name": "LinearWarmup", "num_warmup_steps": 100},
        n_batches=len(data_silo.loaders["train"]),
        n_epochs=n_epochs,
        grad_acc_steps=1,
        device=device
    )

    # 6. Feed everything to the Trainer, which keeps care of growing our model and evaluates it from time to time
    trainer = Trainer(
        model=model,
        optimizer=optimizer,
        data_silo=data_silo,
        epochs=n_epochs,
        n_gpu=n_gpu,
        lr_schedule=lr_schedule,
        evaluate_every=evaluate_every,
        device=device,
    )

    # 7. Let it grow! Watch the tracked metrics live on the public mlflow server: https://public-mlflow.deepset.ai
    trainer.train()

    # 8. Hooray! You have a model. Store it:
    save_dir = Path("../saved_models/dpr-tutorial")
    model.save(save_dir)
    processor.save(save_dir)

    # 9. Evaluate
    test_data_loader = data_silo.get_data_loader("test")
    if test_data_loader is not None:
        evaluator_test = Evaluator(
            data_loader=test_data_loader, tasks=data_silo.processor.tasks, device=device)
        model.connect_heads_with_processor(processor.tasks)
        test_result = evaluator_test.eval(model)

dense_passage_retrieval()
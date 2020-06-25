from typing import List, Any
from abc import ABC
import logging


logger = logging.getLogger(__name__)



class Pred(ABC):
    """
    Abstract base class for predictions of every task
    """

    def __init__(self,
                 id: str,
                 prediction: List[Any],
                 context: str):
        self.id = id
        self.prediction = prediction
        self.context = context

    def to_json(self):
        raise NotImplementedError


class QACandidate:
    """
    A single QA candidate answer.
    """

    def __init__(self,
                 answer_type: str,
                 score: str,
                 offset_answer_start: int,
                 offset_answer_end: int,
                 offset_unit: str,
                 aggregation_level: str,
                 probability: float=None,
                 n_passages_in_doc: int=None,
                 passage_id: str=None,
                 ):
        """
        :param answer_type: The category that this answer falls into e.g. "no_answer", "yes", "no" or "span"
        :param score: The score representing the model's confidence of this answer
        :param offset_answer_start: The index of the start of the answer span (whether it is char or tok is stated in self.offset_unit)
        :param offset_answer_end: The index of the start of the answer span (whether it is char or tok is stated in self.offset_unit)
        :param offset_unit: States whether the offsets refer to character or token indices
        :param aggregation_level: States whether this candidate and its indices are on a passage level (pre aggregation) or on a document level (post aggregation)
        :param probability: The probability the model assigns to the answer
        :param n_passages_in_doc: Number of passages that make up the document
        :param passage_id: The id of the passage which contains this candidate answer
        """

        # self.answer_type can be "no_answer", "yes", "no" or "span"
        self.answer_type = answer_type
        self.score = score
        self.probability = probability

        # If self.answer_type is "span", self.answer is a string answer (generated by self.span_to_string())
        # Otherwise, it is None
        self.answer = None
        self.offset_answer_start = offset_answer_start
        self.offset_answer_end = offset_answer_end

        # If self.answer_type is in ["yes", "no"] then self.answer_support is a text string
        # If self.answer is a string answer span or self.answer_type is "no_answer", answer_support is None
        self.answer_support = None
        self.offset_answer_support_start = None
        self.offset_answer_support_end = None

        # self.context is the document or passage where the answer is found
        self.context = None
        self.offset_context_start = None
        self.offset_context_end = None

        # Offset unit is either "token" or "char"
        # Aggregation level is either "doc" or "passage"
        self.offset_unit = offset_unit
        self.aggregation_level = aggregation_level

        self.n_passages_in_doc = n_passages_in_doc
        self.passage_id = passage_id

    def span_to_string(self, token_offsets: List[int], clear_text: str):
        """
        Generates a string answer span using self.offset_answer_start and self.offset_answer_end. If the candidate
        is a no answer, an empty string is returned

        :param token_offsets: A list of ints which give the start character index of the corresponding token
        :param clear_text: The text from which the answer span is to be extracted
        :return: The string answer span, followed by the start and end character indices
        """

        assert self.offset_unit == "token"

        start_t = self.offset_answer_start
        end_t = self.offset_answer_end

        # If it is a no_answer prediction
        if start_t == -1 and end_t == -1:
            return "", 0, 0

        n_tokens = len(token_offsets)

        # We do this to point to the beginning of the first token after the span instead of
        # the beginning of the last token in the span
        end_t += 1

        # Predictions sometimes land on the very final special token of the passage. But there are no
        # special tokens on the document level. We will just interpret this as a span that stretches
        # to the end of the document
        end_t = min(end_t, n_tokens)

        start_ch = token_offsets[start_t]
        # i.e. pointing at the END of the last token
        if end_t == n_tokens:
            end_ch = len(clear_text)
        else:
            end_ch = token_offsets[end_t]
        return clear_text[start_ch: end_ch].strip(), start_ch, end_ch

    def add_cls(self, predicted_class: str):
        """
        Adjust the final QA prediction depending on the prediction of the classification head (e.g. for binary answers in NQ)
        Currently designed so that the QA head's prediction will always be preferred over the Classification head

        :param predicted_class: The predicted class e.g. "yes", "no", "no_answer", "span"
        """

        if predicted_class in ["yes", "no"] and self.answer != "no_answer":
            self.answer_support = self.answer
            self.answer = predicted_class
            self.answer_type = predicted_class
            self.offset_answer_support_start = self.offset_answer_start
            self.offset_answer_support_end = self.offset_answer_end

    def to_doc_level(self, start, end):
        """ Populate the start and end indices with document level indices. Changes aggregation level to 'document'"""
        self.offset_answer_start = start
        self.offset_answer_end = end
        self.aggregation_level = "document"

    def add_answer(self, string):
        """ Set the answer string. This method will check that the answer given is valid given the start
        and end indices that are stored in the object. """
        if string == "":
            self.answer = "no_answer"
            if self.offset_answer_start != -1 or self.offset_answer_end != -1:
                logger.error(f"Something went wrong in tokenization. We have start and end offsets: "
                             f"{self.offset_answer_start, self.offset_answer_end} with an empty answer. "
                             f"\nContext: {self.context}")
        else:
            self.answer = string
            if self.offset_answer_start == -1 or self.offset_answer_end == -1:
                logger.error(f"Something went wrong in tokenization. We have start and end offsets: "
                             f"{self.offset_answer_start, self.offset_answer_end} with answer: {string}. "
                             f"\nContext: {self.context}")

    def to_list(self):
        return [self.answer, self.offset_answer_start, self.offset_answer_end, self.score, self.passage_id]


class QAPred(Pred):
    """ A set of QA predictions for a passage or a document. The candidates are stored in QAPred.prediction which is a
    list of QACandidate objects. Also contains all attributes needed to convert the object into json format and also
    to create a context window for a UI
    """

    def __init__(self,
                 id: str,
                 prediction: List[QACandidate],
                 context: str,
                 question: str,
                 token_offsets: List[int],
                 context_window_size: int,
                 aggregation_level: str,
                 no_answer_gap: float,
                 n_passages: int,
                 ground_truth_answer: str = None,
                 answer_types: List[str] = []):
        """
        :param id: The id of the passage or document
        :param prediction: A list of QACandidate objects for the given question and document
        :param context: The text passage from which the answer can be extracted
        :param question: The question being posed
        :param token_offsets: A list of ints indicating the start char index of each token
        :param context_window_size: The number of chars in the text window around the answer
        :param aggregation_level: States whether this candidate and its indices are on a passage level (pre aggregation) or on a document level (post aggregation)
        :param no_answer_gap: How much the QuestionAnsweringHead.no_ans_boost needs to change to turn a no_answer to a positive answer
        :param n_passages: Number of passages in the context document
        :param ground_truth_answer: Ground truth answers
        :param answer_types: List of answer_types supported by this task e.g. ["span", "yes_no", "no_answer"]
        """
        super().__init__(id, prediction, context)
        self.question = question
        self.token_offsets = token_offsets
        self.context_window_size = context_window_size
        self.aggregation_level = aggregation_level
        self.answer_types = answer_types
        self.ground_truth_answer = ground_truth_answer
        self.no_answer_gap = no_answer_gap
        self.n_passages = n_passages

    def to_json(self, squad=False):
        """
        Converts the information stored in the object into a json format.

        :param squad: If True, no_answers are represented by the empty string instead of "no_answer"
        :return:
        """

        answers = self.answers_to_json(self.id, squad)
        ret = {
            "task": "qa",
            "predictions": [
                {
                    "question": self.question,
                    "question_id": self.id,
                    "ground_truth": self.ground_truth_answer,
                    "answers": answers,
                    "no_ans_gap": self.no_answer_gap, # Add no_ans_gap to current no_ans_boost for switching top prediction
                }
            ],
        }
        return ret

    def answers_to_json(self, id, squad=False):
        """
        Convert all answers into a json format

        :param id: ID of the question document pair
        :param squad: If True, no_answers are represented by the empty string instead of "no_answer"
        :return:
        """

        ret = []

        # iterate over the top_n predictions of the one document
        for qa_candidate in self.prediction:
            string = qa_candidate.answer

            _, ans_start_ch, ans_end_ch = qa_candidate.span_to_string(self.token_offsets, self.context)
            context_string, context_start_ch, context_end_ch = self.create_context(ans_start_ch, ans_end_ch, self.context)
            if squad and string == "no_answer":
                    string = ""
            curr = {"score": qa_candidate.score,
                    "probability": None,
                    "answer": string,
                    "offset_answer_start": ans_start_ch,
                    "offset_answer_end": ans_end_ch,
                    "context": context_string,
                    "offset_context_start": context_start_ch,
                    "offset_context_end": context_end_ch,
                    "document_id": id}
            ret.append(curr)
        return ret


    def create_context(self, ans_start_ch, ans_end_ch, clear_text):
        """
        Extract from the clear_text a window that contains the answer and some amount of text on either
        side of the answer. Useful for cases where the answer and its surrounding context needs to be
        displayed in a UI.

        :param ans_start_ch: Start character index of the answer
        :param ans_end_ch: End character index of the answer
        :param clear_text: The text from which the answer is extracted
        :return:
        """
        if ans_start_ch == 0 and ans_end_ch == 0:
            return "", 0, 0
        else:
            len_text = len(clear_text)
            midpoint = int((ans_end_ch - ans_start_ch) / 2) + ans_start_ch
            half_window = int(self.context_window_size / 2)
            context_start_ch = midpoint - half_window
            context_end_ch = midpoint + half_window
            # if we have part of the context window overlapping start or end of the passage,
            # we'll trim it and use the additional chars on the other side of the answer
            overhang_start = max(0, -context_start_ch)
            overhang_end = max(0, context_end_ch - len_text)
            context_start_ch -= overhang_end
            context_start_ch = max(0, context_start_ch)
            context_end_ch += overhang_start
            context_end_ch = min(len_text, context_end_ch)
        context_string = clear_text[context_start_ch: context_end_ch]
        return context_string, context_start_ch, context_end_ch

    def to_squad_eval(self):
        return self.to_json(squad=True)

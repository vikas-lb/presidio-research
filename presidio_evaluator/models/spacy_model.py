from typing import List

import spacy

from presidio_evaluator.data_objects import PRESIDIO_SPACY_ENTITIES
from presidio_evaluator import InputSample
from presidio_evaluator.models import BaseModel


class SpacyModel(BaseModel):
    def __init__(
        self,
        model: spacy.language.Language = None,
        model_name: str = None,
        entities_to_keep: List[str] = None,
        verbose: bool = False,
        labeling_scheme: str = "BIO",
        translate_to_spacy_entities=True,
    ):
        super().__init__(
            entities_to_keep=entities_to_keep,
            verbose=verbose,
            labeling_scheme=labeling_scheme,
        )

        if model is None:
            if model_name is None:
                raise ValueError("Either model_name or model object must be supplied")
            self.model = spacy.load(model_name)
        else:
            self.model = model

        self.translate_to_spacy_entities = translate_to_spacy_entities
        if self.translate_to_spacy_entities:
            print(
                "Translating entites using this dictionary: {}".format(
                    PRESIDIO_SPACY_ENTITIES
                )
            )

    def predict(self, sample: InputSample) -> List[str]:
        if self.translate_to_spacy_entities:
            sample.translate_input_sample_tags()

        doc = self.model(sample.full_text)
        tags = self.get_tags_from_doc(doc)
        if len(doc) != len(sample.tokens):
            print("mismatch between input tokens and new tokens")

        return tags

    @staticmethod
    def get_tags_from_doc(doc):
        return [token.ent_type_ if token.ent_type_ != "" else "O" for token in doc]

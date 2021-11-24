from typing import List, Optional

import spacy
import srsly
from spacy.tokens import Token
from spacy.training import docs_to_json, iob_to_biluo
from tqdm import tqdm

from presidio_evaluator import span_to_tag, tokenize

SPACY_PRESIDIO_ENTITIES = {
    "ORG": "ORGANIZATION",
    "NORP": "ORGANIZATION",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "PERSON": "PERSON",
    "LOCATION": "LOCATION",
    "ORGANIZATION": "ORGANIZATION",
}
PRESIDIO_SPACY_ENTITIES = {
    "ORGANIZATION": "ORG",
    "COUNTRY": "GPE",
    "CITY": "GPE",
    "LOCATION": "GPE",
    "PERSON": "PERSON",
    "FIRST_NAME": "PERSON",
    "LAST_NAME": "PERSON",
    "NATION_MAN": "GPE",
    "NATION_WOMAN": "GPE",
    "NATION_PLURAL": "GPE",
    "NATIONALITY": "GPE",
    "GPE": "GPE",
    "ORG": "ORG",
}


class Span:
    """
    Holds information about the start, end, type nad value
    of an entity in a text
    """

    def __init__(self, entity_type, entity_value, start_position, end_position):
        self.entity_type = entity_type
        self.entity_value = entity_value
        self.start_position = start_position
        self.end_position = end_position

    def intersect(self, other, ignore_entity_type: bool):
        """
        Checks if self intersects with a different Span
        :return: If interesecting, returns the number of
        intersecting characters.
        If not, returns 0
        """

        # if they do not overlap the intersection is 0
        if (
            self.end_position < other.start_position
            or other.end_position < self.start_position
        ):
            return 0

        # if we are accounting for entity type a diff type means intersection 0
        if not ignore_entity_type and (self.entity_type != other.entity_type):
            return 0

        # otherwise the intersection is min(end) - max(start)
        return min(self.end_position, other.end_position) - max(
            self.start_position, other.start_position
        )

    def __repr__(self):
        return (
            f"Type: {self.entity_type}, "
            f"value: {self.entity_value}, "
            f"start: {self.start_position}, "
            f"end: {self.end_position}"
        )

    def __eq__(self, other):
        return (
            self.entity_type == other.entity_type
            and self.entity_value == other.entity_value
            and self.start_position == other.start_position
            and self.end_position == other.end_position
        )

    def __hash__(self):
        return hash(
            (
                "entity_type",
                self.entity_type,
                "entity_value",
                self.entity_value,
                "start_position",
                self.start_position,
                "end_position",
                self.end_position,
            )
        )

    @classmethod
    def from_json(cls, data):
        return cls(**data)


class SimpleSpacyExtensions:
    def __init__(self, **kwargs):
        """
        Serialization of Spacy Token extensions.
        see https://spacy.io/api/token#set_extension
        :param kwargs: dictionary of spacy extensions and their values
        """
        self.__dict__.update(kwargs)

    def to_dict(self):
        return self.__dict__


class SimpleToken:
    """
    A class mimicking the Spacy Token class, for serialization purposes
    """

    def __init__(
        self,
        text,
        idx,
        tag_=None,
        pos_=None,
        dep_=None,
        lemma_=None,
        spacy_extensions: SimpleSpacyExtensions = None,
        **kwargs,
    ):
        self.text = text
        self.idx = idx
        self.tag_ = tag_
        self.pos_ = pos_
        self.dep_ = dep_
        self.lemma_ = lemma_

        # serialization for Spacy extensions:
        if spacy_extensions is None:
            self._ = SimpleSpacyExtensions()
        else:
            self._ = spacy_extensions
        self.params = kwargs

    @classmethod
    def from_spacy_token(cls, token):

        if isinstance(token, SimpleToken):
            return token

        elif isinstance(token, Token):

            if token._ and token._._extensions:
                extensions = list(token._.token_extensions.keys())
                extension_values = {
                    extension: token._.__getattr__(extension)
                    for extension in extensions
                }

                spacy_extensions = SimpleSpacyExtensions(**extension_values)
            else:
                spacy_extensions = None

            return cls(
                text=token.text,
                idx=token.idx,
                tag_=token.tag_,
                pos_=token.pos_,
                dep_=token.dep_,
                lemma_=token.lemma_,
                spacy_extensions=spacy_extensions,
            )

    def to_dict(self):
        return {
            "text": self.text,
            "idx": self.idx,
            "tag_": self.tag_,
            "pos_": self.pos_,
            "dep_": self.dep_,
            "lemma_": self.lemma_,
            "_": self._.to_dict(),
        }

    def __repr__(self):
        return self.text

    @classmethod
    def from_json(cls, data):

        if "_" in data:
            data["spacy_extensions"] = SimpleSpacyExtensions(**data["_"])
        return cls(**data)


class InputSample(object):
    def __init__(
        self,
        full_text: str,
        spans: Optional[List[Span]] = None,
        masked: Optional[str] = None,
        tokens: Optional[List[SimpleToken]] = None,
        tags: Optional[List[str]] = None,
        create_tags_from_span=True,
        scheme="IO",
        metadata=None,
        template_id=None,
    ):
        """
        Hold all the information needed for evaluation in the
        presidio-evaluator framework.

        :param full_text: The raw text of this sample
        :param masked: Masked version of the raw text (desired output)
        :param spans: List of spans for entities
        :param create_tags_from_span: True if tags (tokens+taks) should be added
        :param scheme: IO, BIO/IOB or BILOU. Only applicable if span_to_tag=True
        :param tokens: list of items of type SimpleToken
        :param tags: list of strings representing the label for each token,
        given the scheme
        :param metadata: A dictionary of additional metadata on the sample,
        in the English (or other language) vocabulary
        :param template_id: Original template (utterance) of sample, in case it was generated
        """
        if tags is None:
            tags = []
        if tokens is None:
            tokens = []
        self.full_text = full_text
        self.masked = masked
        self.spans = spans or []
        self.metadata = metadata

        # generated samples have a template from which they were generated
        if not template_id and self.metadata:
            self.template_id = self.metadata.get("Template#")
        else:
            self.template_id = template_id

        if create_tags_from_span:
            tokens, tags = self.get_tags(scheme)
        self.tokens = tokens
        self.tags = tags

    def __repr__(self):
        return (
            f"Full text: {self.full_text}\n"
            f"Spans: {self.spans}\n"
            f"Tokens: {self.tokens}\n"
            f"Tags: {self.tags}\n"
        )

    def to_dict(self):

        return {
            "full_text": self.full_text,
            "masked": self.masked,
            "spans": [span.__dict__ for span in self.spans],
            "tokens": [
                SimpleToken.from_spacy_token(token).to_dict() for token in self.tokens
            ],
            "tags": self.tags,
            "template_id": self.template_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, data):
        if "spans" in data:
            data["spans"] = [Span.from_json(span) for span in data["spans"]]
        if "tokens" in data:
            data["tokens"] = [SimpleToken.from_json(val) for val in data["tokens"]]
        return cls(**data, create_tags_from_span=False)

    def get_tags(self, scheme="IOB"):
        start_indices = [span.start_position for span in self.spans]
        end_indices = [span.end_position for span in self.spans]
        tags = [span.entity_type for span in self.spans]
        tokens = tokenize(self.full_text)

        labels = span_to_tag(
            scheme=scheme,
            text=self.full_text,
            tag=tags,
            start=start_indices,
            end=end_indices,
            tokens=tokens,
        )

        return tokens, labels

    def to_conll(self, translate_tags):

        conll = []
        for i, token in enumerate(self.tokens):
            if translate_tags:
                label = self.translate_tag(
                    self.tags[i], PRESIDIO_SPACY_ENTITIES, ignore_unknown=True
                )
            else:
                label = self.tags[i]
            conll.append(
                {
                    "text": token.text,
                    "pos": token.pos_,
                    "tag": token.tag_,
                    "Template#": self.metadata["Template#"],
                    "gender": self.metadata["Gender"],
                    "country": self.metadata["Country"],
                    "label": label,
                },
            )

        return conll

    def get_template_id(self):
        return self.metadata["Template#"]

    @staticmethod
    def create_conll_dataset(dataset, translate_tags=True, to_bio=True):
        import pandas as pd

        conlls = []
        for i, sample in enumerate(dataset):
            if to_bio:
                sample.bilou_to_bio()
            conll = sample.to_conll(translate_tags=translate_tags)
            for token in conll:
                token["sentence"] = i
                conlls.append(token)
        return pd.DataFrame(conlls)

    def to_spacy(self, entities=None, translate_tags=True):
        entities = [
            (span.start_position, span.end_position, span.entity_type)
            for span in self.spans
            if (entities is None) or (span.entity_type in entities)
        ]
        new_entities = []
        if translate_tags:
            for entity in entities:
                new_tag = self.translate_tag(
                    entity[2], PRESIDIO_SPACY_ENTITIES, ignore_unknown=True
                )
                new_entities.append((entity[0], entity[1], new_tag))
        else:
            new_entities = entities
        return self.full_text, {"entities": new_entities}

    @classmethod
    def from_spacy_doc(cls, doc, map_spacy_entities_to_presidio=True, scheme="BILUO"):
        if scheme not in  ("BILUO","BILOU","BIO","IOB"):
            raise ValueError("scheme should be one of \"BILUO\",\"BILOU\",\"BIO\",\"IOB\"")

        spans = []
        for ent in doc.ents:
            entity_type = (
                cls.rename_from_spacy_tags(ent.label_)
                if map_spacy_entities_to_presidio
                else ent.label_
            )
            span = Span(
                entity_type=entity_type,
                entity_value=ent.text,
                start_position=ent.start_char,
                end_position=ent.end_char,
            )
            spans.append(span)

        tags = [f"{token.ent_iob_}-{token.ent_type_}" if token.ent_iob_ != "O" else "O" for token in doc]
        if scheme in ("BILUO", "BILOU"):
            tags = iob_to_biluo(tags)

        return cls(
            full_text=doc.text,
            masked=None,
            spans=spans,
            tokens=doc,
            tags=tags,
            create_tags_from_span=False,
            scheme=scheme
        )

    @staticmethod
    def create_spacy_dataset(
        dataset, entities=None, sort_by_template_id=False, translate_tags=True
    ):
        def template_sort(x):
            return x.metadata["Template#"]

        if sort_by_template_id:
            dataset.sort(key=template_sort)

        return [
            sample.to_spacy(entities=entities, translate_tags=translate_tags)
            for sample in dataset
        ]

    def to_spacy_json(self, entities=None, translate_tags=True):
        token_dicts = []
        for i, token in enumerate(self.tokens):
            if entities:
                tag = self.tags[i] if self.tags[i][2:] in entities else "O"
            else:
                tag = self.tags[i]

            if translate_tags:
                tag = self.translate_tag(
                    tag, PRESIDIO_SPACY_ENTITIES, ignore_unknown=True
                )
            token_dicts.append({"orth": token.text, "tag": token.tag_, "ner": tag})

        return {
            "raw": self.full_text,
            "sentences": [{"tokens": token_dicts}],
        }

    def to_spacy_doc(self):
        doc = self.tokens
        spacy_spans = []
        for span in self.spans:
            start_token = [
                token.i for token in self.tokens if token.idx == span.start_position
            ][0]
            end_token = [
                token.i
                for token in self.tokens
                if token.idx + len(token.text) == span.end_position
            ][0] + 1
            spacy_span = spacy.tokens.span.Span(
                doc, start=start_token, end=end_token, label=span.entity_type
            )
            spacy_spans.append(spacy_span)
        doc.ents = spacy_spans
        return doc

    @staticmethod
    def create_spacy_json(
        dataset, entities=None, sort_by_template_id=False, translate_tags=True
    ):
        def template_sort(x):
            return x.metadata["Template#"]

        if sort_by_template_id:
            dataset.sort(key=template_sort)

        json_str = []
        for i, sample in tqdm(enumerate(dataset)):
            paragraph = sample.to_spacy_json(
                entities=entities, translate_tags=translate_tags
            )
            json_str.append({"id": i, "paragraphs": [paragraph]})

        return json_str

    @staticmethod
    def translate_tags(tags, dictionary, ignore_unknown):
        """
        Translates entity types from one set to another
        :param tags: list of entities to translate, e.g. ["LOCATION","O","PERSON"]
        :param dictionary: Dictionary of old tags to new tags
        :param ignore_unknown: Whether to put "O" when word not in dictionary or keep old entity type
        :return: list of translated entities
        """
        return [
            InputSample.translate_tag(tag, dictionary, ignore_unknown)
            for tag in tags
        ]

    @staticmethod
    def translate_tag(tag, dictionary, ignore_unknown):
        has_prefix = len(tag) > 2 and tag[1] == "-"
        no_prefix = tag[2:] if has_prefix else tag
        if no_prefix in dictionary.keys():
            return (
                tag[:2] + dictionary[no_prefix] if has_prefix else dictionary[no_prefix]
            )
        if ignore_unknown:
            return "O"
        else:
            return tag

    def bilou_to_bio(self):
        new_tags = []
        for tag in self.tags:
            new_tag = tag
            has_prefix = len(tag) > 2 and tag[1] == "-"
            if has_prefix:
                if tag[0] == "U":
                    new_tag = "B" + tag[1:]
                elif tag[0] == "L":
                    new_tag = "I" + tag[1:]
            new_tags.append(new_tag)

        self.tags = new_tags

    @staticmethod
    def rename_from_spacy_tags(spacy_tags, ignore_unknown=False):
        return InputSample.translate_tags(
            spacy_tags, SPACY_PRESIDIO_ENTITIES, ignore_unknown=ignore_unknown
        )

    @staticmethod
    def rename_to_spacy_tags(tags, ignore_unknown=True):
        return InputSample.translate_tags(
            tags, PRESIDIO_SPACY_ENTITIES, ignore_unknown=ignore_unknown
        )

    @staticmethod
    def write_spacy_json_from_docs(dataset, filename="spacy_output.json"):
        docs = [sample.to_spacy_doc() for sample in dataset]
        srsly.write_json(filename, [spacy.training.docs_to_json(docs)])

    def to_flair(self):
        for token, i in enumerate(self.tokens):
            return f"{token} {token.pos_} {self.tags[i]}"

    def translate_input_sample_tags(self, dictionary=None, ignore_unknown=True):
        if dictionary is None:
            dictionary = PRESIDIO_SPACY_ENTITIES
        self.tags = InputSample.translate_tags(
            self.tags, dictionary, ignore_unknown=ignore_unknown
        )
        for span in self.spans:
            if span.entity_value in PRESIDIO_SPACY_ENTITIES:
                span.entity_value = PRESIDIO_SPACY_ENTITIES[span.entity_value]
            elif ignore_unknown:
                span.entity_value = "O"

    @staticmethod
    def create_flair_dataset(dataset):
        return [sample.to_flair() for sample in dataset]

import functools
import operator

from flex.exceptions import ValidationError
from flex.utils import chain_reduce_partial
from flex.context_managers import ErrorCollection
from flex.http import (
    Request,
)
from flex.constants import (
    QUERY,
    PATH,
    EMPTY,
    HEADER,
)
from flex.parameters import (
    filter_parameters,
    merge_parameter_lists,
    dereference_parameter_list,
)
from flex.validation.parameter import (
    validate_query_parameters,
)
from flex.validation.header import (
    construct_header_validators,
)
from flex.validation.path import (
    generate_path_parameters_validator,
)
from flex.validation.common import (
    validate_object,
    generate_value_processor,
)


def validate_operation(request, validators):
    with ErrorCollection() as errors:
        for key, validator in validators.items():
            try:
                validator(request)
            except ValidationError as err:
                errors[key].add_error(err.detail)


def validate_request_content_type(request, content_types):
    assert isinstance(request, Request)
    # TODO: is it correct to skip validation for a null content_type?
    if request.content_type and request.content_type not in content_types:
        raise ValidationError(
            'Invalid content type `{0}`.  Must be one of `{1}`.'.format(
                request.content_type, content_types,
            ),
        )


def generate_request_content_type_validator(consumes, **kwargs):
    validator = functools.partial(
        validate_request_content_type,
        content_types=consumes,
    )
    return validator


def generate_query_parameters_validator(query_parameters, context):
    query_parameter_validator = functools.partial(
        validate_query_parameters,
        query_parameters=query_parameters,
        context=context,
    )
    return chain_reduce_partial(
        operator.attrgetter('query_data'),
        query_parameter_validator,
    )


def generate_header_validator(headers, context, **kwargs):
    """
    Generates a validation function that will validate a dictionary of headers.
    """
    validators = {}
    for header_definition in headers:
        header_processor = generate_value_processor(
            context=context,
            **header_definition
        )
        header_validator = functools.partial(
            validate_object,
            validators=construct_header_validators(header_definition, context=context),
        )
        validators[header_definition['name']] = chain_reduce_partial(
            operator.methodcaller('get', header_definition['name'], EMPTY),
            header_processor,
            header_validator,
        )
    return functools.partial(validate_object, validators=validators)


def generate_parameters_validator(api_path, path_definition, parameters,
                                  context, **kwargs):
    """
    Generates a validator function to validate.

    - request.path against the path parameters.
    - request.query against the query parameters.
    - request.headers against the header parameters.
    - TODO: request.body against the body parameters.
    - TODO: request.formData against any form data.
    """
    # TODO: figure out how to merge this with the same code in response
    # validation.
    validators = {}
    parameter_definitions = context.get('parameters', {})
    path_level_parameters = dereference_parameter_list(
        path_definition.get('parameters', []),
        parameter_definitions,
    )
    operation_level_parameters = dereference_parameter_list(
        parameters,
        parameter_definitions,
    )

    all_parameters = merge_parameter_lists(
        path_level_parameters,
        operation_level_parameters,
    )

    # PATH
    in_path_parameters = filter_parameters(all_parameters, in_=PATH)
    validators['path'] = chain_reduce_partial(
        operator.attrgetter('path'),
        generate_path_parameters_validator(api_path, in_path_parameters, context),
    )

    # QUERY
    in_query_parameters = filter_parameters(all_parameters, in_=QUERY)
    validators['query'] = generate_query_parameters_validator(in_query_parameters, context)

    # HEADERS
    in_header_parameters = filter_parameters(all_parameters, in_=HEADER)
    validators['headers'] = chain_reduce_partial(
        operator.attrgetter('headers'),
        generate_header_validator(in_header_parameters, context),
    )

    return functools.partial(validate_object, validators=validators)


validator_mapping = {
    'consumes': generate_request_content_type_validator,
    'parameters': generate_parameters_validator,
    'headers': generate_header_validator,
}


def construct_operation_validators(api_path, path_definition, operation_definition, context):
    """
    - consumes (did the request conform to the content types this api consumes)
    - produces (did the response conform to the content types this endpoint produces)
    - parameters (did the parameters of this request validate)
      TODO: move path parameter validation to here, because each operation
            can override any of the path level parameters.
    - schemes (was the request scheme correct)
    - security: TODO since security isn't yet implemented.
    """
    validators = {}

    # sanity check
    assert 'context' not in operation_definition
    assert 'api_path' not in operation_definition
    assert 'path_definition' not in operation_definition

    for key in operation_definition.keys():
        if key not in validator_mapping:
            # TODO: is this the right thing to do?
            continue
        validators[key] = validator_mapping[key](
            context=context,
            api_path=api_path,
            path_definition=path_definition,
            **operation_definition
        )

    # Global defaults
    if 'consumes' in context and 'consumes' not in validators:
        validators['consumes'] = validator_mapping['consumes'](**context)
    if 'parameters' in path_definition and 'parameters' not in validators:
        validators['parameters'] = validator_mapping['parameters'](
            context=context,
            api_path=api_path,
            path_definition=path_definition,
            parameters=path_definition['parameters'],
            **operation_definition
        )

    return validators

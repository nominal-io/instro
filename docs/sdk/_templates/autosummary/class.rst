{{ objname | escape | underline }}

.. currentmodule:: {{ module }}

{#- Data types (pydantic models, dataclasses, enums) read best as one page with
    their fields inline; behavioural classes get member tables with a page per
    member. `members` is dir(obj), so these markers identify the kind. #}
{% if "model_fields" in members %}
.. autopydantic_model:: {{ objname }}
{% elif "__dataclass_fields__" in members or "__members__" in members %}
.. autoclass:: {{ objname }}
   :members:
{% else %}
.. autoclass:: {{ objname }}
   :no-members:
   :no-inherited-members:

{% block attributes %}
{% if attributes %}
.. rubric:: Attributes

.. autosummary::
   :toctree:
   :nosignatures:
{% for item in attributes %}
   ~{{ name }}.{{ item }}
{%- endfor %}
{% endif %}
{% endblock %}

{% block methods %}
{#- extra_methods (conf.py autosummary_context) opts private methods back in. #}
{% set public_methods = (methods | reject("equalto", "__init__") | list) + extra_methods.get(fullname, []) %}
{% if public_methods %}
.. rubric:: Methods

.. autosummary::
   :toctree:
   :nosignatures:
{% for item in public_methods %}
   ~{{ name }}.{{ item }}
{%- endfor %}
{% endif %}
{% endblock %}
{% endif %}

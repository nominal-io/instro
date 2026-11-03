{{ objname | escape | underline }}

.. currentmodule:: {{ module }}

{#- Data types (pydantic models, dataclasses, enums) read best as one page with
    their fields inline; behavioural classes get member tables followed by the
    members' details on the same page. `members` is dir(obj), so these markers identify the kind. #}
{% if "model_fields" in members %}
.. autopydantic_model:: {{ objname }}
{% elif "__dataclass_fields__" in members or "__members__" in members %}
.. autoclass:: {{ objname }}
   :members:
{% else %}
{#- Behavioural classes: summary tables up top, every member's details below
    them on the same page. The tables sit in the autoclass body so they render
    after the class docstring and before the members; their links resolve to
    the in-page anchors. extra_methods (conf.py autosummary_context) opts
    private methods back in. #}
{% set extra = extra_methods.get(fullname, []) %}
{% set public_methods = (methods | reject("equalto", "__init__") | list) + extra %}
.. autoclass:: {{ objname }}
{%- if extra %}
   :private-members: {{ extra | join(", ") }}
{%- endif %}
{% if attributes %}
   .. rubric:: Attributes

   .. autosummary::
      :nosignatures:
{% for item in attributes %}
      ~{{ name }}.{{ item }}
{%- endfor %}
{% endif %}
{% if public_methods %}
   .. rubric:: Methods

   .. autosummary::
      :nosignatures:
{% for item in public_methods %}
      ~{{ name }}.{{ item }}
{%- endfor %}
{% endif %}
{% endif %}

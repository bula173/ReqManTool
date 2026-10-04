export type AttributeType = 'string' | 'text' | 'integer' | 'boolean' | 'enum';

export interface AttributeDefinition {
  id: string;
  name: string;
  type: AttributeType;
  required: boolean;
  default: unknown;
  choices: string[] | null;
  system: boolean;
}

export interface LinkConfiguration {
  id: string;
  name: string;
  source_module_id: string;
  target_module_id: string;
}

export interface View {
  id: string;
  name: string;
  attribute_ids: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  attributes: AttributeDefinition[];
  link_configurations: LinkConfiguration[];
  views: View[];
}

export interface Module {
  id: string;
  name: string;
  prefix: string;
  description: string;
}

export interface Requirement {
  id: string;
  attributes: Record<string, unknown>;
  parent_id: string | null;
  order: number;
}

export interface ApiErrorBody {
  detail: string;
}

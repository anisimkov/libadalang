## vim: filetype=makoada

function ${accessor_name}
  (Node    : ${node_type};
   Value_P : ${field_type.c_type(capi).name}_Ptr) return int
is
   N : constant AST_Node := Unwrap (Node);
begin
   if N.all in ${astnode.name()}_Type'Class then
      declare
         Typed_Node : constant ${astnode.name()} := ${astnode.name()} (N);
      begin
          % if is_enum(field_type):
              Value_P.all := ${field_type.c_type(capi).name}
                (${field_type.name()}'Pos (Typed_Node.F_${field.name}));
          % elif is_bool(field_type):
              Value_P.all := int (Boolean'Pos (Typed_Node.F_${field.name}));
          % elif is_ast_node(field_type):
              Value_P.all := Wrap (AST_Node (Typed_Node.F_${field.name}));
          % elif is_token_type(field_type):
              Value_P.all := Wrap (Typed_Node.F_${field.name}'Access);
          % else:
              Value_P.all := Typed_Node.F_${field.name};
          % endif
          return 1;
      end;
   else
      return 0;
   end if;
end ${accessor_name};

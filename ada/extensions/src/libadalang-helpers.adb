------------------------------------------------------------------------------
--                                                                          --
--                                Libadalang                                --
--                                                                          --
--                     Copyright (C)      2019, AdaCore                     --
--                                                                          --
-- Libadalang is free software;  you can redistribute it and/or modify  it  --
-- under terms of the GNU General Public License  as published by the Free  --
-- Software Foundation;  either version 3,  or (at your option)  any later  --
-- version.   This  software  is distributed in the hope that it  will  be  --
-- useful but  WITHOUT ANY WARRANTY;  without even the implied warranty of  --
-- MERCHANTABILITY  or  FITNESS  FOR  A PARTICULAR PURPOSE.                 --
--                                                                          --
-- As a special  exception  under  Section 7  of  GPL  version 3,  you are  --
-- granted additional  permissions described in the  GCC  Runtime  Library  --
-- Exception, version 3.1, as published by the Free Software Foundation.    --
--                                                                          --
-- You should have received a copy of the GNU General Public License and a  --
-- copy of the GCC Runtime Library Exception along with this program;  see  --
-- the files COPYING3 and COPYING.RUNTIME respectively.  If not, see        --
-- <http://www.gnu.org/licenses/>.                                          --
------------------------------------------------------------------------------

with Ada.Command_Line;
with Ada.Containers.Synchronized_Queue_Interfaces;
with Ada.Containers.Unbounded_Synchronized_Queues;
with Ada.Text_IO; use Ada.Text_IO;
with Ada.Unchecked_Deallocation;

with GNAT.Traceback.Symbolic;

with GNATCOLL.Projects;  use GNATCOLL.Projects;
with GNATCOLL.Traces;
with GNATCOLL.VFS;       use GNATCOLL.VFS;

with Libadalang.Project_Provider; use Libadalang.Project_Provider;

package body Libadalang.Helpers is

   function "+" (S : String) return Unbounded_String
                 renames To_Unbounded_String;
   function "+" (S : Unbounded_String) return String renames To_String;

   procedure Print_Error (Message : String);
   --  Shortcut for Put_Line (Standard_Error, Message)

   package String_QI is new Ada.Containers.Synchronized_Queue_Interfaces
     (Unbounded_String);
   package String_Queues is new Ada.Containers.Unbounded_Synchronized_Queues
     (String_QI);

   -----------------
   -- Print_Error --
   -----------------

   procedure Print_Error (Message : String) is
   begin
      --  If Message's last character is a newline, leave it out and let
      --  Put_Line append it. This avoids the additional line break that
      --  Text_IO would append later otherwise.

      if Message = "" then
         return;
      elsif Message (Message'Last) = ASCII.LF then
         Put_Line
           (Standard_Error, Message (Message'First .. Message'Last - 1));
      else
         Put (Standard_Error, Message);
      end if;
   end Print_Error;

   ---------------
   -- Abort_App --
   ---------------

   procedure Abort_App (Message : String := "") is
   begin
      if Message /= "" then
         Put_Line (Standard_Error, Message);
      end if;
      raise Abort_App_Exception;
   end Abort_App;

   package body App is

      --  The following protected object is used for a job to signal to the
      --  other jobs that it has aborted. In this case, the other jobs must
      --  finish processing their current analysis unit and stop there.

      protected Abortion is
         procedure Signal_Abortion;
         function Abort_Signaled return Boolean;
      private
         Abort_Signaled_State : Boolean := False;
      end Abortion;

      protected body Abortion is
         procedure Signal_Abortion is
         begin
            Abort_Signaled_State := True;
         end Signal_Abortion;

         function Abort_Signaled return Boolean is
         begin
            return Abort_Signaled_State;
         end Abort_Signaled;
      end Abortion;

      --------------------
      -- Dump_Exception --
      --------------------

      procedure Dump_Exception (E : Ada.Exceptions.Exception_Occurrence) is
      begin
         if Args.No_Traceback.Get then
            --  Do not use Exception_Information nor Exception_Message. The
            --  former includes tracebacks and the latter includes line
            --  numbers in Libadalang: both are bad for testcase output
            --  consistency.
            Put_Line ("> " & Ada.Exceptions.Exception_Name (E));
            New_Line;

         elsif Args.Sym_Traceback.Get then
            Put_Line (Ada.Exceptions.Exception_Message (E));
            Put_Line (GNAT.Traceback.Symbolic.Symbolic_Traceback (E));

         else
            Put_Line ("> " & Ada.Exceptions.Exception_Information (E));
            New_Line;
         end if;
      end Dump_Exception;

      ---------
      -- Run --
      ---------

      procedure Run is

         package String_Vectors is new Ada.Containers.Vectors
           (Positive, Unbounded_String);

         Project : Project_Tree_Access;
         --  Reference to the loaded project tree, if any. Null otherwise.

         UFP : Unit_Provider_Reference;
         --  When project file handling is enabled, corresponding unit provider

         type App_Job_Context_Array_Access is access App_Job_Context_Array;
         procedure Free is new Ada.Unchecked_Deallocation
           (App_Job_Context_Array, App_Job_Context_Array_Access);

         App_Ctx      : aliased App_Context;
         Job_Contexts : App_Job_Context_Array_Access;

         Files : String_Vectors.Vector;
         Queue : String_Queues.Queue;

         task type Main_Task_Type is
            entry Start (ID : Job_ID);
            entry Stop;
         end Main_Task_Type;

         task body Main_Task_Type is
            F   : Unbounded_String;
            JID : Job_ID;
         begin
            --  Wait for the signal to start jobs

            accept Start (ID : Job_ID) do
               JID := ID;
            end Start;

            --  We can now do our processings and invoke user callbacks when
            --  appropriate.

            declare
               Job_Ctx : App_Job_Context renames Job_Contexts (JID);

               type Any_Step is (Setup, In_Unit, Tear_Down);
               Step : Any_Step := Setup;
            begin
               Job_Setup (Job_Ctx);
               Step := In_Unit;
               loop
                  --  Stop as soon as we noticed that another job requested
                  --  abortion.

                  if Abortion.Abort_Signaled then
                     Job_Ctx.Aborted := True;
                     exit;
                  end if;

                  --  Pick the next file and process it

                  select
                     Queue.Dequeue (F);
                  or
                     delay 0.1;
                     exit;
                  end select;

                  declare
                     Unit : constant Analysis_Unit :=
                        Job_Ctx.Analysis_Ctx.Get_From_File (+F);
                  begin
                     Process_Unit (Job_Ctx, Unit);
                     Job_Ctx.Units_Processed.Append (Unit);
                  end;
               end loop;
               Step := Tear_Down;
               Job_Tear_Down (Job_Ctx);

            --  Make sure to handle properly uncaught errors (they have nowhere
            --  to propagate once here) and abortion requests.

            exception
               when Abort_App_Exception =>
                  Job_Ctx.Aborted := True;
                  Abortion.Signal_Abortion;

               when E : others =>
                  Job_Ctx.Aborted := True;
                  Abortion.Signal_Abortion;
                  declare
                     Context : constant String :=
                       (case Step is
                        when Setup     => "in Job_Setup",
                        when In_Unit   => "in Process_Unit for " & (+F),
                        when Tear_Down => "in Job_Tear_Down");
                  begin
                     Put_Line
                       (Standard_Error,
                        "Unhandled error " & Context
                        & " (job" & JID'Image & ")");
                     Dump_Exception (E);
                  end;
            end;

            accept Stop do
               null;
            end Stop;
         end Main_Task_Type;

      begin
         --  Setup traces from config file
         GNATCOLL.Traces.Parse_Config_File;

         if not Args.Parser.Parse then
            return;
         end if;

         --  Handle project file
         if Length (Args.Project_File.Get) > 0 then
            declare
               Filename : constant String := +Args.Project_File.Get;
               Env      : Project_Environment_Access;
               List     : File_Array_Access;
            begin
               Project := new Project_Tree;
               Initialize (Env);

               --  Set scenario variables
               for Assoc of Args.Scenario_Vars.Get loop
                  declare
                     A        : constant String := +Assoc;
                     Eq_Index : Natural := A'First;
                  begin
                     while Eq_Index <= A'Last
                       and then A (Eq_Index) /= '=' loop
                        Eq_Index := Eq_Index + 1;
                     end loop;
                     if Eq_Index not in A'Range then
                        Abort_App ("Invalid scenario variable: -X" & A);
                     end if;
                     Change_Environment
                       (Env.all,
                        A (A'First .. Eq_Index - 1),
                        A (Eq_Index + 1 .. A'Last));
                  end;
               end loop;

               --  Load the project tree, and beware of loading errors. Wrap
               --  the project in a unit provider.
               begin
                  Project.Load
                    (Root_Project_Path => Create (+Filename),
                     Env               => Env,
                     Errors            => Print_Error'Access);
               exception
                  when Invalid_Project =>
                     Free (Project);
                     Free (Env);
                     Abort_App;
               end;
               UFP := Create_Project_Unit_Provider_Reference
                 (Project, Project.Root_Project, Env);

               --  Build the list of source files to process
               if Args.Files.Get'Length > 0 then
                  for F of Args.Files.Get loop
                     Files.Append (F);
                  end loop;

               else
                  --  If no explicit file list was passed, get a sorted list of
                  --  source files to get deterministic execution.
                  List := Project.Root_Project.Source_Files;

                  Sort (List.all);

                  for F of List.all loop
                     declare
                        FI        : constant File_Info := Project.Info (F);
                        Full_Name : Filesystem_String renames F.Full_Name.all;
                        Name      : constant String := +Full_Name;
                     begin
                        if FI.Language = "ada" then
                           Files.Append (+Name);
                        end if;
                     end;
                  end loop;
                  Unchecked_Free (List);

               end if;
            end;
         else
            --  No project passed: process the files passed explicitly
            for F of Args.Files.Get loop
               Files.Append (F);
            end loop;
         end if;

         --  Initialize contexts

         App_Ctx := (Project => Project);
         Job_Contexts := new App_Job_Context_Array'
           (1 .. Job_ID (Args.Jobs.Get) =>
            (App_Ctx => App_Ctx'Unchecked_Access, others => <>));
         for JID in Job_Contexts.all'Range loop
            Job_Contexts (JID) :=
              (ID              => JID,
               App_Ctx         => App_Ctx'Unchecked_Access,
               Analysis_Ctx    => Create_Context
                                    (Charset       => +Args.Charset.Get,
                                     Unit_Provider => UFP),
               Units_Processed => <>,
               Aborted         => False);
         end loop;

         --  Finally, create all jobs, and one context per job to process unit
         --  files.

         App_Setup (App_Ctx, Job_Contexts.all);
         declare
            Task_Pool : array (Job_Contexts.all'Range) of Main_Task_Type;
         begin
            for JID in Task_Pool'Range loop
               Task_Pool (JID).Start (JID);
            end loop;

            for F of Files loop
               Queue.Enqueue (F);
            end loop;

            for T of Task_Pool loop
               T.Stop;
            end loop;
         end;
         App_Tear_Down (App_Ctx, Job_Contexts.all);
         Free (Job_Contexts);

      exception
         when Abort_App_Exception =>
            Free (Job_Contexts);
            Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Failure);
      end Run;
   end App;

end Libadalang.Helpers;